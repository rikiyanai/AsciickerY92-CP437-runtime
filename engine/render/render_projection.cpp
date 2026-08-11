// render_projection.cpp — projection/unprojection extracted from render.cpp
#include <math.h>
#include "render_internal.h"
#include "matrix.h"


// WHY ProjectCoords: Maps a 3D world position to 2D screen coordinates
// (in AnsiCell units). Used by gameplay code to position UI elements
// (name labels, HP bars) relative to world objects. Supports both
// isometric and perspective projection paths.
bool ProjectCoords(Renderer* r, const float pos[3], int view[3])
{
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
