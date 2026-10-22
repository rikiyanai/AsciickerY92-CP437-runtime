// render_debug_observation.cpp — Render-side report adapters
//
// SEE ALSO: render_internal.h, render_observation_builder.h

#include "render_internal.h"
#include "render_observation_builder.h"

TrackedRemoteClampReport BuildTrackedRemoteClampReportForSprite(
	const SampleBuffer& sample_buffer,
	const SpriteRenderBuf* buf,
	int width, int height)
{
	TrackedRemoteClampInputs in = {};
	in.sprite_s_pos_z = buf ? buf->s_pos[2] : 0;
	for (int i = 0; i < 4; i++)
		in.center_samples[i].spare = -1;
	if (!buf)
		return BuildTrackedRemoteClampReport(in);

	const int cx = buf->s_pos[0];
	const int cy = buf->s_pos[1];
	if (cx >= 0 && cx < width && cy >= 0 && cy < height)
	{
		in.center_in_bounds = true;
		Sample* center_ll = sample_buffer.ptr + (2 * cy + 2) * (2 * width + 4) + 2 * cx + 2;
		Sample* center_lr = center_ll + 1;
		Sample* center_ul = center_ll + (2 * width + 4);
		Sample* center_ur = center_ul + 1;
		Sample* center_samples[4] = { center_ll, center_lr, center_ul, center_ur };
		for (int i = 0; i < 4; i++)
		{
			Sample* sc = center_samples[i];
			in.center_samples[i].spare = sc ? (int)sc->spare : -1;
			in.center_samples[i].height = sc ? sc->height : 0.0f;
			in.center_samples[i].valid =
				sc && !(sc->spare & 0x8) && sc->height > -999999.0f;
		}
	}

	return BuildTrackedRemoteClampReport(in);
}
