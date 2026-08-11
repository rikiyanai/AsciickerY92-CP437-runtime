// render_hud_overlay.cpp — UI overlay rendering (HP bars, etc.)
//
// Extracted from render_scene.cpp.
// SEE ALSO: render_hud_overlay.h

#include "render_internal.h"
#include "render_hud_overlay.h"

void RenderHpBars(
	Renderer* r,
	AnsiCell* out_ptr,
	int width, int height,
	TrackedNpcRenderReport* tracked_npc_report)
{
	for (int s = 0; s < r->sprites; s++)
	{
		SpriteRenderBuf* buf = r->sprites_alloc + s;
		if (buf->refl)
			continue;

		if (buf->character && (uintptr_t)buf->character > 1)
		{
			// Character-backed player or NPC
			int dy = 10;
			int y = buf->s_pos[1] + dy;

			static const float ds = 2.0f * (1.0f * 3.0f) / VISUAL_CELLS * 0.5f;
			static const float dz_dy = (float)(HEIGHT_SCALE / (cos(30.0f * M_PI / 180.0f) * HEIGHT_CELLS * ds));
			float t = dy * dz_dy + buf->s_pos[2];

			int lt_red   = 16 + 0 + 0 * 6 + 5 * 36;
			int lt_orange = 16 + 0 + 2 * 6 + 5 * 36;
			int dk_red   = 16 + 0 + 0 * 6 + 3 * 36;
			int dk_orange = 16 + 0 + 1 * 6 + 3 * 36;
			uint8_t fg = buf->character_clr ? lt_orange : lt_red;
			uint8_t bk = buf->character_clr ? dk_orange : dk_red;

			AnsiCell ac;
			ac.bk = bk;
			ac.fg = fg;

			if (y >= 0 && y < height)
			{
				for (int bx = -2; bx <= 2; bx++)
				{
					int x = bx + buf->s_pos[0];
					if (x < 0 || x >= width)
						continue;

					Sample* test_ll = r->sample_buffer.ptr + (2 * y + 2) * (2 * width + 4) + 2 * x + 2;
					Sample* test_lr = r->sample_buffer.ptr + (2 * y + 2) * (2 * width + 4) + 2 * x + 3;
					Sample* test_ul = r->sample_buffer.ptr + (2 * y + 3) * (2 * width + 4) + 2 * x + 2;
					Sample* test_ur = r->sample_buffer.ptr + (2 * y + 3) * (2 * width + 4) + 2 * x + 3;

					if (!test_ul->DepthTest_RO(t) && !test_ur->DepthTest_RO(t) &&
						!test_ll->DepthTest_RO(t) && !test_lr->DepthTest_RO(t))
					{
						continue;
					}

					// Placeholder:Character-backed bars always empty for now.
					ac.gl = ' ';
					out_ptr[x + width * y] = ac;
				}
			}
		}
		else if ((uintptr_t)buf->character == 1 && buf->npc_max_hp > 0)
		{
			// Snapshot NPC bar (server-authoritative, no local Character*)
			if (buf->tracked_npc && tracked_npc_report)
				tracked_npc_report->hp_bar_expected = 1;

			int dy = 10;
			int y = buf->s_pos[1] + dy;

			static const float ds2 = 2.0f * (1.0f * 3.0f) / VISUAL_CELLS * 0.5f;
			static const float dz_dy2 = (float)(HEIGHT_SCALE / (cos(30.0f * M_PI / 180.0f) * HEIGHT_CELLS * ds2));
			float t = dy * dz_dy2 + buf->s_pos[2];

			int lt_red = 16 + 0 + 0 * 6 + 5 * 36;
			int dk_red = 16 + 0 + 0 * 6 + 3 * 36;

			AnsiCell ac;
			ac.bk = (uint8_t)dk_red;
			ac.fg = (uint8_t)lt_red;

			int hp = buf->npc_hp;
			int max_hp = buf->npc_max_hp;
			if (max_hp <= 0) max_hp = 1;

			if (y >= 0 && y < height)
			{
				for (int bx = -2; bx <= 2; bx++)
				{
					int x = bx + buf->s_pos[0];
					if (x < 0 || x >= width)
						continue;

					Sample* test_ll = r->sample_buffer.ptr + (2 * y + 2) * (2 * width + 4) + 2 * x + 2;
					Sample* test_lr = r->sample_buffer.ptr + (2 * y + 2) * (2 * width + 4) + 2 * x + 3;
					Sample* test_ul = r->sample_buffer.ptr + (2 * y + 3) * (2 * width + 4) + 2 * x + 2;
					Sample* test_ur = r->sample_buffer.ptr + (2 * y + 3) * (2 * width + 4) + 2 * x + 3;

					if (!test_ul->DepthTest_RO(t) && !test_ur->DepthTest_RO(t) &&
						!test_ll->DepthTest_RO(t) && !test_lr->DepthTest_RO(t))
					{
						continue;
					}

					bool l  = (bx + 2) * max_hp * 2 + 0 < hp * 10;
					bool r2 = (bx + 2) * max_hp * 2 + max_hp < hp * 10;

					if (r2)
						ac.gl = 219; // full
					else if (l)
						ac.gl = 221; // half
					else
						ac.gl = ' '; // none

					out_ptr[x + width * y] = ac;
					if (buf->tracked_npc && tracked_npc_report)
						tracked_npc_report->hp_bar_drawn = 1;
				}
			}
		}
	}
}
