// render_stage_shadow.cpp — Stage 4: player blob shadow on terrain
//
// Extracted from render_scene.cpp.
// SEE ALSO: render_stage_shadow.h

#include "render_internal.h"
#include "matrix.h"

void RenderStageShadow(
	Renderer* r,
	int dw, int dh,
	int width,
	const float pos[3],
	const int scene_shift[2],
	Material* matlib)
{
	double* inv_tm = r->inv_tm;

	int sh_x = width + 1 + scene_shift[0] * 2;

	for (int y = 0; y < dh; y++)
	{
		int left = sh_x - 5;
		int right = sh_x + 5;
		if (left < 0)
			left = 0;
		if (right >= dw)
			right = dw - 1;

		for (int x = left; x <= right; x++)
		{
			Sample* s = r->sample_buffer.ptr + x + y * dw;
			if (abs(s->height - pos[2]) <= 64)
			{
				double screen_space[] = { (double)x, (double)y, s->height, 1.0 };
				double world_space[4];

				Product(inv_tm, screen_space, world_space);
				double dx = world_space[0] / HEIGHT_CELLS - pos[0];
				double dy = world_space[1] / HEIGHT_CELLS - pos[1];
				double sq_xy = dx * dx + dy * dy;

				if (sq_xy <= 2.00)
				{
					int dz = (int)(2 * (pos[2] - s->height) + 2 * sq_xy);
					if (dz < 180)
						dz = 180;
					if (dz > 180)
						dz = 255;

					if (s->spare & 0x8)
					{
						s->diffuse = s->diffuse * dz / 255;
					}
					else
					{
						int mat = s->visual & 0xFF;
						int shd = (s->visual >> 8) & 0x7F;

						int rc = (matlib[mat].shade[1][shd].bg[0] * 249 + 1014) >> 11;
						int gc = (matlib[mat].shade[1][shd].bg[1] * 249 + 1014) >> 11;
						int bc = (matlib[mat].shade[1][shd].bg[2] * 249 + 1014) >> 11;
						s->visual = rc | (gc << 5) | (bc << 10);
						s->spare |= 0x8;
						s->spare &= ~0x44;
						s->diffuse = dz;
					}
				}
			}
		}
	}
}
