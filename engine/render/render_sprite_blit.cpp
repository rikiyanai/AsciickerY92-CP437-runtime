// render_sprite_blit.cpp — Sprite rendering/blitting
//
// Extracted from engine/render.cpp.
// SEE ALSO: render.h, render_internal.h

#include "render_internal.h"
#include "facing_space.h"
#include "game.h"  // Human/Character/Item definition (type completeness only — no global access)
#include "matrix.h"
#include "snapshot_npc_sprite_data_tag.h"

void Renderer::RenderSprite(Inst* inst, Sprite* s, float pos[3], float yaw, int anim, int frame, int reps[4], void* cookie /*Renderer*/)
{
	Renderer* r = (Renderer*)cookie;

	void* sprite_data = GetInstSpriteData(inst);
	uintptr_t sprite_data_tag = (uintptr_t)sprite_data;
	const bool snapshot_npc_body =
		sprite_data_tag == kSnapshotNpcBodySpriteDataTag;
	Character* h = snapshot_npc_body ? 0 : (Character*)sprite_data;
	const bool has_fi = r->frame_input && r->frame_input->valid;
	int tracked_remote_pid = has_fi ? r->frame_input->tracking.tracked_remote_pid : -1;
	bool tracked_remote = false;
	bool tracked_npc = false;
	if (!global_refl_mode && has_fi && h)
	{
		Human* candidate = (Human*)h;
		const Human* others = r->frame_input->remote.others;
		int max_clients = r->frame_input->remote.max_clients;
		if (others && candidate >= others && candidate < others + max_clients)
		{
			int this_pid = (int)(candidate - others);
			tracked_remote = (tracked_remote_pid >= 0 && this_pid == tracked_remote_pid);
		}
		if (tracked_remote)
		{
			r->queue_report.query_seen = 1;
			r->queue_report.inst_cookie_match = 1;
		}
	}
	if (!global_refl_mode && has_fi && snapshot_npc_body &&
		r->frame_input->tracking.tracked_npc_entity_id >= 0)
	{
		for (int i = 0; i < 64; i++)
		{
			if (r->frame_input->snapshot_npcs.visuals[i].inst == inst &&
				(int)r->frame_input->snapshot_npcs.visuals[i].entity_id == r->frame_input->tracking.tracked_npc_entity_id)
			{
				tracked_npc = true;
				break;
			}
		}
	}

	bool is_item = anim < 0;
	if (is_item)
	{
		int purpose = frame;
		if (purpose != Item::WORLD)
			return;
		anim = frame = 0;

		static int _reps[4] = { -1,-1,-1,-1 };
		reps = _reps;
	}

	if (!s || s->anims <= 0 || s->angles <= 0)
	{
		if (tracked_remote)
			r->queue_report.queue_skip_reason = 1;
		return;
	}
	if (anim < 0 || anim >= s->anims)
		anim = 0;
	if (s->anim[anim].length <= 0)
	{
		if (tracked_remote)
			r->queue_report.queue_skip_reason = 1;
		return;
	}

	if (global_refl_mode && s->projs == 1)
	{
		if (tracked_remote)
			r->queue_report.queue_skip_reason = 2;
		return;
	}

	// transform and append to sprite render list
	if (r->sprites == r->sprites_alloc_size)
	{
		r->sprites_alloc_size += 16;
		r->sprites_alloc = (SpriteRenderBuf*)realloc(r->sprites_alloc, sizeof(SpriteRenderBuf) * r->sprites_alloc_size);
	}

	SpriteRenderBuf* buf = r->sprites_alloc + r->sprites;

	float w_pos[3] = { pos[0] * HEIGHT_CELLS, pos[1] * HEIGHT_CELLS, pos[2] };

	float vx = w_pos[0], vy = w_pos[1], vz = w_pos[2];
	float viewer_dist; // {vx,vy,vz}  r->pos
	float eye_to_vtx[3] =
	{
		vx - r->view_pos[0],
		vy - r->view_pos[1],
		vz - r->view_pos[2],
	};

	viewer_dist = DotProduct(eye_to_vtx, r->view_dir);

	if (global_refl_mode)
	{
		if (r->perspective) // #if PERSPECTIVE_TEST
		{
			if (viewer_dist > 0)
			{
				// todo: smooth fade
				float max_scale = 1.33f;
				float hi_scale = 1.25f;
				float lo_scale = 1 / hi_scale;
				float min_scale = 1 / max_scale;

				if (!h)
				if (viewer_dist > max_scale || viewer_dist < min_scale)
				{
					if (tracked_remote)
						r->queue_report.queue_skip_reason = 3;
					return;
				}

				float alpha = 1.0;

				if (viewer_dist < lo_scale)
					alpha = (viewer_dist - min_scale) / (lo_scale - min_scale);
				else
				if (viewer_dist > hi_scale)
					alpha = (viewer_dist - max_scale) / (hi_scale - max_scale);

				buf->alpha = (int)(alpha * 255 + 0.5f);

				float fx = (float)(r->mul[0] * vx + r->mul[2] * vy + r->add[0]);
				float fy = (float)(r->mul[1] * vx + r->mul[3] * vy + r->mul[5] * vz + r->add[1]);

				float recp_dist = 1.0f/viewer_dist;

				fx = (fx - r->view_ofs[0]) * recp_dist + r->view_ofs[0];
				fy = (fy - r->view_ofs[1]) * recp_dist + r->view_ofs[1];

				int tx = (int)floorf(fx + 0.5f);
				int ty = (int)floorf(fy + 0.5f);

				// convert from samples to cells
				buf->s_pos[0] = (tx - 1) >> 1;
				buf->s_pos[1] = (ty - 1) >> 1;
				buf->s_pos[2] = (int)(2*r->water) - ((int)floorf(w_pos[2] + 0.5f) + HEIGHT_SCALE / 2);
			}
			else
			{
				if (tracked_remote)
					r->queue_report.queue_skip_reason = 4;
				return;
			}
		}
		else // #else
		{
			//if (r->int_flag)
			{
				int tx = (int)floor(r->mul[0] * w_pos[0] + r->mul[2] * w_pos[1] + 0.5 + r->add[0]);
				int ty = (int)floor(r->mul[1] * w_pos[0] + r->mul[3] * w_pos[1] + r->mul[5] * w_pos[2] + 0.5 + r->add[1]);

				// convert from samples to cells
				buf->s_pos[0] = (tx - 1) >> 1;
				buf->s_pos[1] = (ty - 1) >> 1;
				buf->s_pos[2] = (int)(2*r->water) - ((int)floorf(w_pos[2] + 0.5f) + HEIGHT_SCALE / 2);
			}
			/*
			else
			{
				int tx = (int)floor(r->mul[0] * w_pos[0] + r->mul[2] * w_pos[1] + 0.5) + r->add[0];
				int ty = (int)floor(r->mul[1] * w_pos[0] + r->mul[3] * w_pos[1] + r->mul[5] * w_pos[2] + 0.5) + r->add[1];

				// convert from samples to cells
				buf->s_pos[0] = (tx - 1) >> 1;
				buf->s_pos[1] = (ty - 2) >> 1;
				buf->s_pos[2] = (int)2 * r->water - ((int)floorf(w_pos[2] + 0.5) + HEIGHT_SCALE / 4);
			}
			*/
		} // #endif
	}
	else
	{
		if (r->perspective) // #if PERSPECTIVE_TEST
		{
			if (viewer_dist > 0)
			{
				// todo: smooth fade
				float max_scale = 1.33f;
				float hi_scale = 1.25f;
				float lo_scale = 1 / hi_scale;
				float min_scale = 1 / max_scale;

				if (!h)
				if (viewer_dist > max_scale || viewer_dist < min_scale)
				{
					if (tracked_remote)
						r->queue_report.queue_skip_reason = 3;
					return;
				}

				float alpha = 1.0;

				if (viewer_dist < lo_scale)
					alpha = (viewer_dist - min_scale) / (lo_scale - min_scale);
				else
				if (viewer_dist > hi_scale)
					alpha = (viewer_dist - max_scale) / (hi_scale - max_scale);

				buf->alpha = (int)(alpha * 255 + 0.5f);

				float fx = (float)(r->mul[0] * vx + r->mul[2] * vy + r->add[0]);
				float fy = (float)(r->mul[1] * vx + r->mul[3] * vy + r->mul[5] * vz + r->add[1]);

				float recp_dist = 1.0f/viewer_dist;

				fx = (fx - r->view_ofs[0]) * recp_dist + r->view_ofs[0];
				fy = (fy - r->view_ofs[1]) * recp_dist + r->view_ofs[1];

				int tx = (int)floorf(fx + 0.5f);
				int ty = (int)floorf(fy + 0.5f);

				// convert from samples to cells
				buf->s_pos[0] = (tx - 1) >> 1;
				buf->s_pos[1] = (ty - 1) >> 1;
				buf->s_pos[2] = (int)floorf(w_pos[2] + 0.5f) + HEIGHT_SCALE / 2;
			}
			else
			{
				if (tracked_remote)
					r->queue_report.queue_skip_reason = 4;
				return;
			}
		}
		else // #else
		{
			//if (r->int_flag)
			{
				int tx = (int)floor(r->mul[0] * w_pos[0] + r->mul[2] * w_pos[1] + 0.5 + r->add[0]);
				int ty = (int)floor(r->mul[1] * w_pos[0] + r->mul[3] * w_pos[1] + r->mul[5] * w_pos[2] + 0.5 + r->add[1]);

				// convert from samples to cells
				buf->s_pos[0] = (tx - 1) >> 1;
				buf->s_pos[1] = (ty - 1) >> 1;
				buf->s_pos[2] = (int)floorf(w_pos[2] + 0.5f) + HEIGHT_SCALE / 2;
			}
			/*
			else
			{
				int tx = (int)floor(r->mul[0] * w_pos[0] + r->mul[2] * w_pos[1] + 0.5) + r->add[0];
				int ty = (int)floor(r->mul[1] * w_pos[0] + r->mul[3] * w_pos[1] + r->mul[5] * w_pos[2] + 0.5) + r->add[1];

				// convert from samples to cells
				buf->s_pos[0] = (tx - 1) >> 1;
				buf->s_pos[1] = (ty - 2) >> 1;
				buf->s_pos[2] = (int)floorf(w_pos[2] + 0.5) + HEIGHT_SCALE / 4;
			}
			*/
		} // #endif
	}

	// `yaw` is the legacy sprite-compensated facing direction from
	// FacingMovementStep() (S=0°, E=90°, N=180°, W=-90°).
	// The compensation maps S→0° directly to row 0 (front) for south-first
	// character sheets, so all sprites use the same raw angle index — no
	// south-first render offset needed. See facing_space.h header.
	int ang = FacingSpriteAngleIndex(yaw, r->yaw, s->angles);
	
	buf->sprite = s;
	buf->angle = ang;
	buf->anim = anim;
	if (is_item)
		buf->frame = frame;
	else
		buf->frame = AnimateSpriteInst(inst, r->stamp);
	buf->reps[0] = reps[0];
	buf->reps[1] = reps[1];
	buf->reps[2] = reps[2];
	buf->reps[3] = reps[3];
	buf->refl = global_refl_mode;
	buf->tracked_remote = tracked_remote;
	buf->tracked_npc = tracked_npc;
	buf->is_local_player = false;
	buf->remote_pid = -1;
	if (has_fi && h)
	{
		const Human* others = r->frame_input->remote.others;
		int mc = r->frame_input->remote.max_clients;
		Human* candidate = (Human*)h;
		if (others && candidate >= others && candidate < others + mc)
			buf->remote_pid = (int16_t)(candidate - others);
		else if (candidate == (const Human*)r->frame_input->remote.local_player)
			buf->is_local_player = true;
	}

	buf->character = h;
	buf->character_clr = (h && !snapshot_npc_body) ? (uint8_t)h->clr : 0;

	buf->npc_hp = 0;
	buf->npc_max_hp = 0;
	buf->npc_presentation_kind_id = 0;
	buf->npc_life_state = 0;
	if (snapshot_npc_body && has_fi)
	{
		for (int i = 0; i < 64; i++)
		{
			if (r->frame_input->snapshot_npcs.visuals[i].inst == inst)
			{
				buf->npc_hp = r->frame_input->snapshot_npcs.visuals[i].hp;
				buf->npc_max_hp = r->frame_input->snapshot_npcs.visuals[i].max_hp;
				buf->npc_presentation_kind_id = r->frame_input->snapshot_npcs.visuals[i].presentation_kind_id;
				if (i < r->frame_input->snapshot_npcs.npc_count &&
					r->frame_input->snapshot_npcs.npcs[i].entity_id == r->frame_input->snapshot_npcs.visuals[i].entity_id)
					buf->npc_life_state = r->frame_input->snapshot_npcs.npcs[i].life_state;
				break;
			}
		}
	}

	buf->dist = viewer_dist;
	if (tracked_remote)
		r->queue_report.queue_enqueued = 1;

	r->sprites++;
}

SpriteBlitDiagnostics Renderer::RenderSprite(AnsiCell* ptr, int width, int height, Sprite* s, bool refl, int anim, int frame, int angle, int pos[3])
{
	SpriteBlitDiagnostics diag;
	diag.blit_pos_z = (float)pos[2];
	diag.water_plane_z = water;
	bool drew_any = false;
	int diag_depth_pass = 0;
	int diag_depth_fail = 0;
	int diag_depth_fail_mesh = 0;
	int diag_depth_fail_mesh_samples = 0;
	int diag_candidate_cells = 0;
	int diag_water_reject_cells = 0;
	float diag_candidate_min_z = 0.0f;
	float diag_candidate_max_z = 0.0f;

	auto MarkTrackedNpcReject = [&](int reason)
	{
		diag.reject_reason = reason;
		if (reason == 3 || reason == 4)
			diag.clip_reject = true;
	};

	if (!s || s->anims <= 0 || s->angles <= 0)
	{
		MarkTrackedNpcReject(1);
		return diag;
	}
	if (anim < 0)
		anim = 0;
	if (anim >= s->anims)
		anim = s->anims - 1;
	int len = s->anim[anim].length;
	if (len <= 0)
	{
		anim = 0;
		len = s->anim[anim].length;
		if (len <= 0)
		{
			MarkTrackedNpcReject(2);
			return diag;
		}
	}
	if (frame < 0)
		frame = 0;
	else
		frame %= len;
	if (angle < 0)
		angle = (angle % s->angles + s->angles) % s->angles;
	else if (angle >= s->angles)
		angle %= s->angles;
	// intersect frame with screen buffer
	int i = frame + angle * len;
	if (refl)
		i += len * s->angles;

	Sprite::Frame* f = s->atlas + s->anim[anim].frame_idx[i];

	// TODO(PIPELINE-FIX): ref[0]/2 and ref[1]/2 assume .xp sprite cells map
	// to 2x supersampled coordinates. If the asset pipeline changes cell size
	// or the DBL define is removed, these divisions produce wrong offsets.
	int dx = f->ref[0] / 2;
	int dy = f->ref[1] / 2;

	int left   = pos[0] - dx;
	int right  = left + f->width;
	int bottom = pos[1] - dy;
	int top    = bottom + f->height;
	diag.unclipped_left = left;
	diag.unclipped_right = right;
	diag.unclipped_bottom = bottom;
	diag.unclipped_top = top;
	diag.frame_width = f->width;
	diag.frame_height = f->height;
	diag.ref_x = f->ref[0];
	diag.ref_y = f->ref[1];
	diag.screen_pos_x = pos[0];
	diag.screen_pos_y = pos[1];

	left = std::max(0, left);
	right = std::min(width, right);
	diag.clip_left = left;
	diag.clip_right = right;

	// FL-3012 owner narrowing: recorder fields such as
	// tracked_npc0_render_head_layer_definition_id/render_layer_count only prove
	// the resolved NPC stack existed upstream. If this reject fires, the first
	// source owner that zeroes the visible body is the render-time clip stage
	// here, not bundle resolution.
	if (left >= right)
	{
		MarkTrackedNpcReject(3);
		return diag;
	}

	bottom = std::max(0, bottom);
	top = std::min(height, top);
	diag.clip_bottom = bottom;
	diag.clip_top = top;

	if (bottom >= top)
	{
		MarkTrackedNpcReject(4);
		return diag;
	}

	int sample_xy = 2 + 2 * (2 + 2 * width + 2);
	int sample_dx = 2;
	int sample_dy = 2 * (2 + 2 * width + 2);
	int sample_ofs[4] = { 0, 1, 2 + 2 * width + 2, 2 + 2 * width + 2 + 1 };

	//static const float height_scale = HEIGHT_SCALE / 1.5; // WHY?????  HS*DBL/ZOOM ?

	// Sprite projection constants defined in sprite_constants.h (SPRITE_ZOOM, SPRITE_SCALE).
	// These must match the values in the main Render() function. If zoom becomes dynamic or
	// scale changes, sprite depth testing will be wrong.
	static const float ds = 2.0 * (SPRITE_ZOOM * SPRITE_SCALE) / VISUAL_CELLS * 0.5 /*we're not dbl_wh*/;
	static const float dz_dy = (float)(HEIGHT_SCALE / (cos(30 * M_PI / 180) * HEIGHT_CELLS * ds));

	for (int y = bottom; y < top; y++)
	{
		int fy = y - pos[1] + dy;
		for (int x = left; x < right; x++)
		{
			int fx = x - pos[0] + dx;
			AnsiCell* dst = ptr + x + width * y;
			const AnsiCell* src = f->cell + fx + fy * f->width;

			int depth_passed = 0;

			Sample* s00 = sample_buffer.ptr + sample_xy + x * sample_dx + y * sample_dy;
			Sample* s01 = s00 + 1;
			Sample* s10 = s00 + 2 + 2 * width + 2;
			Sample* s11 = s10 + 1;

				// spare is in full blocks, ref in half!
				float height = (2 * src->spare + f->ref[2]) * 0.5f * dz_dy + pos[2]; // *height_scale + pos[2]; // transform!
				const bool src_empty =
					(src->bk == SPRITE_TRANSPARENT_INDEX && src->fg == SPRITE_TRANSPARENT_INDEX) ||
					((src->gl == 32 || src->gl == 0) && src->bk == SPRITE_TRANSPARENT_INDEX) ||
					(src->gl == 219 && src->fg == SPRITE_TRANSPARENT_INDEX);
				const bool water_pass = (!refl && height >= water) || (refl && height <= water);
				if (!src_empty)
				{
					if (diag_candidate_cells == 0)
					{
						diag_candidate_min_z = height;
						diag_candidate_max_z = height;
					}
					else
					{
						if (height < diag_candidate_min_z)
							diag_candidate_min_z = height;
						if (height > diag_candidate_max_z)
							diag_candidate_max_z = height;
					}
					diag_candidate_cells++;
					if (!water_pass)
						diag_water_reject_cells++;
				}
				auto DepthPasses = [&](Sample* sample) -> bool
				{
					// FL-3012 spent attempt: user-visible rerun still showed mesh clipping
					// and missing NPC head/arm/limb cells after switching this predicate
					// to Sample::DepthTest_RO(). That behavior is reverted here.
					//
					// Also do not revive the spent FL-576/FL-611 tracked-remote mesh bypass:
					// no mesh-flag special cases, Z rewrites, support retries, or second
					// RenderSprite owner here. The remaining owner is deeper render
					// placement/clipping or mesh/body semantic ownership after a nonzero
					// resolved stack.
					return height >= sample->height;
				};
				auto AcceptDepthSample = [&](Sample* sample, int bit, int& mask)
				{
					if (!DepthPasses(sample))
						return;
					if (height >= sample->height)
						sample->height = height;
					mask |= bit;
				};
				auto RecordDepthOutcome = [&](int mask)
				{
					Sample* samples[4] = { s00, s01, s10, s11 };
					for (int sample_i = 0; sample_i < 4; sample_i++)
					{
						const int bit = 1 << sample_i;
						if ((mask & bit) == 0 && (samples[sample_i]->spare & 0x8))
							diag_depth_fail_mesh_samples++;
					}
					if (mask)
					{
						diag_depth_pass++;
						return;
					}
					diag_depth_fail++;
					if ((s00->spare | s01->spare | s10->spare | s11->spare) & 0x8)
						diag_depth_fail_mesh++;
				};
				if (water_pass)
				{
					// early rejection
					if (src_empty)
					{
						// NOP
					}
				// Swoosh marker constant defined in sprite_constants.h (SPRITE_SWOOSH_INDEX).
				// This convention is baked into .xp sprite assets during loading.
				else
				if (src->fg == SPRITE_SWOOSH_INDEX) // swoosh
				{
					// note: if both fg and bk are swoosh, 
					// case is unified to fg swoosh with glyph 219
					// during sprite loading!

					// sprites MUST be sorted by viewing dir (from furthest to nearest)
					// otherwise swoosh/smoke fx could get overwriten by further sprites!

					int mask = 0;
					if (DepthPasses(s00))
					{
						// s00->height = height;
						mask |= 1;
					}
					if (DepthPasses(s01))
					{
						// s01->height = height;
						mask |= 2;
					}
					if (DepthPasses(s10))
					{
						// s10->height = height;
						mask |= 4;
					}
					if (DepthPasses(s11))
					{
						// s11->height = height;
						mask |= 8;
					}

					RecordDepthOutcome(mask);
					if (!mask)
						continue;
					drew_any = true;

					switch (src->gl)
					{
						case 219: // fullblock
						{
							if (mask == 15)
							{
								dst->bk = LightenColor(dst->bk);
								dst->fg = LightenColor(dst->fg);
								break;
							}

							// no break is intentional here
						}

						default:
							int fg = LightenColor(AverageGlyph(dst, mask));
							if (src->bk == SPRITE_TRANSPARENT_INDEX)
								dst->bk = AverageGlyph(dst, SPRITE_MASK_FULL ^ mask);
							else
								dst->bk = src->bk;
							dst->fg = fg;
							dst->gl = src->gl;
					}
				}
				else
				if (src->bk == SPRITE_SWOOSH_INDEX) // swoosh
				{
					int mask = 0;
					AcceptDepthSample(s00, 1, mask);
					AcceptDepthSample(s01, 2, mask);
					AcceptDepthSample(s10, 4, mask);
					AcceptDepthSample(s11, 8, mask);

					RecordDepthOutcome(mask);
					if (!mask)
						continue;
					drew_any = true;

					switch (src->gl)
					{
						case 0:
						case 32: // spaces
						{
							if (mask == 15)
							{
								dst->bk = LightenColor(dst->bk);
								dst->fg = LightenColor(dst->fg);
								break;
							}

							// no break is intentional here
						}

						default:

							int bk = LightenColor(AverageGlyph(dst, SPRITE_MASK_FULL ^ mask));
							if (src->fg == SPRITE_TRANSPARENT_INDEX)
								dst->fg = AverageGlyph(dst, mask);
							else
								dst->fg = src->fg;

							dst->bk = bk;
							dst->gl = src->gl;
					}
				}
				else
				// full block write with FG & BK
				if (src->bk != SPRITE_TRANSPARENT_INDEX && src->fg != SPRITE_TRANSPARENT_INDEX)
				{
					int mask = 0;
					AcceptDepthSample(s00, 1, mask);
					AcceptDepthSample(s01, 2, mask);
					AcceptDepthSample(s10, 4, mask);
					AcceptDepthSample(s11, 8, mask);

					RecordDepthOutcome(mask);

					if (mask == 0xF)
					{
						drew_any = true;
						*dst = *src;
					}
					else
					if (mask == 0x0)
					{
					}
					else
					if (mask==0x3) // lower
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xC);
						dst->fg = AverageGlyph(src, 0x3);
						dst->gl = 220;
					}
					else
					if (mask == 0xC) // upper
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0x3);
						dst->fg = AverageGlyph(src, 0xC);
						dst->gl = 223;
					}
					else
					if (mask == 0x5) // left
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xA);
						dst->fg = AverageGlyph(src, 0x5);
						dst->gl = 221;
					}
					else
					if (mask == 0xA) // right
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0x5);
						dst->fg = AverageGlyph(src, 0xA);
						dst->gl = 222;
					}
					else
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xF-mask);
						dst->fg = AverageGlyph(dst, mask);
						if (mask == 1 || mask == 2 || mask == 4 || mask == 8)
							dst->gl = 176;
						else
						if (mask == 9 || mask == 6)
							dst->gl = 177;
						else
							dst->gl = 178;
					}
				}
				else
				// full block write with BK
				if (src->bk != SPRITE_TRANSPARENT_INDEX && (src->gl == 32 || src->gl == 0))
				{
					int mask = 0;
					AcceptDepthSample(s00, 1, mask);
					AcceptDepthSample(s01, 2, mask);
					AcceptDepthSample(s10, 4, mask);
					AcceptDepthSample(s11, 8, mask);

					RecordDepthOutcome(mask);

					if (mask == 0xF)
					{
						drew_any = true;
						dst->gl = 219;
						dst->fg = src->bk;
					}
					else
					if (mask == 0x0)
					{
					}
					else
					if (mask==0x3) // lower
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xC);
						dst->fg = src->bk;
						dst->gl = 220;
					}
					else
					if (mask == 0xC) // upper
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0x3);
						dst->fg = src->bk;
						dst->gl = 223;
					}
					else
					if (mask == 0x5) // left
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xA);
						dst->fg = src->bk;
						dst->gl = 221;
					}
					else
					if (mask == 0xA) // right
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0x5);
						dst->fg = src->bk;
						dst->gl = 222;
					}
					else
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xF-mask);
						dst->fg = src->bk;
						if (mask == 1 || mask == 2 || mask == 4 || mask == 8)
							dst->gl = 176;
						else
						if (mask == 9 || mask == 6)
							dst->gl = 177;
						else
							dst->gl = 178;
					}
				}
				else
				// full block write with FG
				if (src->fg != SPRITE_TRANSPARENT_INDEX && src->gl == 219)
				{
					int mask = 0;
					AcceptDepthSample(s00, 1, mask);
					AcceptDepthSample(s01, 2, mask);
					AcceptDepthSample(s10, 4, mask);
					AcceptDepthSample(s11, 8, mask);

					RecordDepthOutcome(mask);

					if (mask == 0xF)
					{
						drew_any = true;
						dst->gl = ' ';// ooh 219;
						dst->fg = src->bk;
						dst->bk = src->fg;
					}
					else
					if (mask == 0x0)
					{
					}
					else
					if (mask==0x3) // lower
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xC);
						dst->fg = src->fg;
						dst->gl = 220;
					}
					else
					if (mask == 0xC) // upper
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0x3);
						dst->fg = src->fg;
						dst->gl = 223;
					}
					else
					if (mask == 0x5) // left
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xA);
						dst->fg = src->fg;
						dst->gl = 221;
					}
					else
					if (mask == 0xA) // right
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0x5);
						dst->fg = src->fg;
						dst->gl = 222;
					}
					else
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xF-mask);
						dst->fg = src->fg;
						if (mask == 1 || mask == 2 || mask == 4 || mask == 8)
							dst->gl = 176;
						else
						if (mask == 9 || mask == 6)
							dst->gl = 177;
						else
							dst->gl = 178;
					}
				}
				else
				// half block transparaent
				if (src->gl >= 220 && src->gl <= 223)
				{
					int mask = 0;
					if (src->bk == SPRITE_TRANSPARENT_INDEX && src->gl == 220 || src->fg == SPRITE_TRANSPARENT_INDEX && src->gl == 223) // lower
					{
						AcceptDepthSample(s00, 1, mask);
						AcceptDepthSample(s01, 2, mask);
					}
					else
					if (src->bk == SPRITE_TRANSPARENT_INDEX && src->gl == 221 || src->fg == SPRITE_TRANSPARENT_INDEX && src->gl == 222) // left
					{
						AcceptDepthSample(s00, 1, mask);
						AcceptDepthSample(s10, 4, mask);
					}
					else
					if (src->bk == SPRITE_TRANSPARENT_INDEX && src->gl == 222 || src->fg == SPRITE_TRANSPARENT_INDEX && src->gl == 221) // right
					{
						AcceptDepthSample(s01, 2, mask);
						AcceptDepthSample(s11, 8, mask);
					}
					else
					if (src->bk == SPRITE_TRANSPARENT_INDEX && src->gl == 223 || src->fg == SPRITE_TRANSPARENT_INDEX && src->gl == 220) // upper
					{
						AcceptDepthSample(s10, 4, mask);
						AcceptDepthSample(s11, 8, mask);
					}

					RecordDepthOutcome(mask);

					int color = src->bk == SPRITE_TRANSPARENT_INDEX ? src->fg : src->bk;
					if (mask == 0x0)
					{
					}
					else
					if (mask==0x3) // lower
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xC);
						dst->fg = color;
						dst->gl = 220;
					}
					else
					if (mask == 0xC) // upper
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0x3);
						dst->fg = color;
						dst->gl = 223;
					}
					else
					if (mask == 0x5) // left
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xA);
						dst->fg = color;
						dst->gl = 221;
					}
					else
					if (mask == 0xA) // right
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0x5);
						dst->fg = color;
						dst->gl = 222;
					}
					else
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xF-mask);
						dst->fg = src->fg;
						dst->gl = 176;
					}
				}
				else
				{
					// something else with transparency
					int mask = 0;
					AcceptDepthSample(s00, 1, mask);
					AcceptDepthSample(s01, 2, mask);
					AcceptDepthSample(s10, 4, mask);
					AcceptDepthSample(s11, 8, mask);

					RecordDepthOutcome(mask);

					if (mask == 0xF)
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xF);
						dst->fg = src->fg;
						dst->gl = src->gl;
					}
					else
					if (mask == 0x0)
					{
					}
					else
					if (mask==0x3) // lower
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xC);
						dst->fg = AverageGlyph(src, 0x3);
						dst->gl = 220;
					}
					else
					if (mask == 0xC) // upper
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0x3);
						dst->fg = AverageGlyph(src, 0xC);
						dst->gl = 223;
					}
					else
					if (mask == 0x5) // left
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xA);
						dst->fg = AverageGlyph(src, 0x5);
						dst->gl = 221;
					}
					else
					if (mask == 0xA) // right
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0x5);
						dst->fg = AverageGlyph(src, 0xA);
						dst->gl = 222;
					}
					else
					{
						drew_any = true;
						dst->bk = AverageGlyph(dst, 0xF-mask);
						dst->fg = AverageGlyph(dst, mask);
						if (mask == 1 || mask == 2 || mask == 4 || mask == 8)
							dst->gl = 176;
						else
						if (mask == 9 || mask == 6)
							dst->gl = 177;
						else
							dst->gl = 178;
					}
				}
			}

			///////////////////////////

			/*
			if (src->bk != SPRITE_TRANSPARENT_INDEX)
			{
				if (src->fg != SPRITE_TRANSPARENT_INDEX)
				{
					// check if at least 2/4 samples passes depth test, update all 4
					// ...

					if (!refl && height >= water || refl && height <= water)
					{
						if (height >= s00->height)
						{
							s00->height = height;
							depth_passed++;
						}
						if (height >= s01->height)
						{
							s01->height = height;
							depth_passed++;
						}
						if (height >= s10->height)
						{
							s10->height = height;
							depth_passed++;
						}
						if (height >= s11->height)
						{
							s11->height = height;
							depth_passed++;
						}
					}

					if (depth_passed >= 3)
					{
						*dst = *src;
						//s00->height = height;
						//s01->height = height;
						//s10->height = height;
						//s11->height = height;
					}
				}
				else
				{
					// check if at least 1/2 bk sample passes depth test, update both
					// ...

					if (!refl && height >= water || refl && height <= water)
					{
						if (height >= s00->height)
						{
							s00->height = height;
							depth_passed++;
						}
						if (height >= s01->height)
						{
							s01->height = height;
							depth_passed++;
						}
						if (height >= s10->height)
						{
							s10->height = height;
							depth_passed++;
						}
						if (height >= s11->height)
						{
							s11->height = height;
							depth_passed++;
						}
					}

					if (depth_passed >= 3)
					{
						if (dst->gl == 0xDC && src->gl == 0xDF || dst->gl == 0xDD && src->gl == 0xDE ||
							dst->gl == 0xDF && src->gl == 0xDC || dst->gl == 0xDE && src->gl == 0xDD)
						{
							dst->fg = src->bk;
						}
						else
						{
							dst->bk = src->bk;
							dst->gl = src->gl;
						}

						// s00->height = height;
						// s01->height = height;
						// s10->height = height;
						// s11->height = height;
					}
				}
			}
			else
			{
				if (src->fg != SPRITE_TRANSPARENT_INDEX)
				{
					// check if at least 1/2 fg samples passes depth test, update both
					// ...
					if (!refl && height >= water || refl && height <= water)
					{
						if (height >= s00->height)
						{
							s00->height = height;
							depth_passed++;
						}
						if (height >= s01->height)
						{
							s01->height = height;
							depth_passed++;
						}
						if (height >= s10->height)
						{
							s10->height = height;
							depth_passed++;
						}
						if (height >= s11->height)
						{
							s11->height = height;
							depth_passed++;
						}
					}

					if (depth_passed >= 3)
					{
						if (dst->gl == 0xDC && src->gl == 0xDF || dst->gl == 0xDD && src->gl == 0xDE ||
							dst->gl == 0xDF && src->gl == 0xDC || dst->gl == 0xDE && src->gl == 0xDD)
						{
							dst->bk = src->fg;
						}
						else
						{
							dst->fg = src->fg;
							dst->gl = src->gl;
						}

						//s00->height = height;
						//s01->height = height;
						//s10->height = height;
						//s11->height = height;
					}
				}
			}
			*/
		}
	}

	// Gather center terrain height for remote0 diagnostics
	{
		int cx = (left + right) / 2;
		int cy = (bottom + top) / 2;
		if (cx >= 0 && cx < width && cy >= 0 && cy < height)
		{
			Sample* sc = sample_buffer.ptr + sample_xy + cx * sample_dx + cy * sample_dy;
			diag.center_terrain_height = sc->height;
			diag.has_center_terrain_height = true;
		}
	}

	diag.drew_any = drew_any;
	diag.depth_pass_cells = diag_depth_pass;
	diag.depth_fail_cells = diag_depth_fail;
	diag.depth_fail_mesh_cells = diag_depth_fail_mesh;
	diag.depth_fail_mesh_samples = diag_depth_fail_mesh_samples;
	diag.candidate_cells = diag_candidate_cells;
	diag.water_reject_cells = diag_water_reject_cells;
	diag.candidate_min_z = diag_candidate_cells > 0 ? diag_candidate_min_z : 0.0f;
	diag.candidate_max_z = diag_candidate_cells > 0 ? diag_candidate_max_z : 0.0f;

	if (!diag.drew_any && diag.reject_reason == 0)
	{
		if (diag.candidate_cells <= 0)
			diag.reject_reason = 5;
		else if (diag.water_reject_cells >= diag.candidate_cells)
			diag.reject_reason = 6;
		else if (diag.depth_pass_cells <= 0 && diag.depth_fail_cells > 0)
			diag.reject_reason = 7;
		else
			diag.reject_reason = 8;
	}

	return diag;
}

// CreateRenderer/DeleteRenderer live in render_core.cpp
