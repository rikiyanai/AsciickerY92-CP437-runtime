// render_scene.cpp — Render() top-level frame function
//
// Extracted from engine/render.cpp.
// SEE ALSO: render.h, render_internal.h

#include "render_internal.h"
#include "matrix.h"
#include "render_stage_resolve.h"
#include "render_debug_observation.h"
#include "render_observation_builder.h"
#include "render_hud_overlay.h"
#include "render_stage_shadow.h"
#include "render_rasterize.h"

extern void* GetMaterialArr();

namespace
{
struct ProjectileLineCellWriter
{
	void operator()(AnsiCell* cell, float, const float* vary) const
	{
		if (!cell || !vary)
			return;
		cell->fg = (uint8_t)vary[0];
		cell->bk = 16;
		cell->gl = (uint8_t)vary[1];
		cell->spare = 0;
	}
};

static void RenderProjectileLines(Renderer* r, AnsiCell* ptr, int width, int height, uint64_t stamp)
{
	if (!r || !r->frame_input || !r->frame_input->valid || !ptr)
		return;
	const RenderFrameInput* input = r->frame_input;
	for (int i = 0; i < input->projectile_line_count && i < RENDER_MAX_PROJECTILE_LINES; i++)
	{
		const RenderProjectileLine* line = &input->projectile_lines[i];
		if (!line->active)
			continue;
		uint64_t elapsed = stamp >= line->spawn_stamp ? stamp - line->spawn_stamp : 0;
		const float travel = elapsed >= 300000 ? 1.0f : (float)elapsed / 300000.0f;
		float tail = travel - 0.18f;
		if (tail < 0.0f)
			tail = 0.0f;
		float moving_from_world[3] = {};
		float moving_to_world[3] = {};
		for (int axis = 0; axis < 3; axis++)
		{
			float delta = line->to[axis] - line->from[axis];
			moving_from_world[axis] = line->from[axis] + delta * tail;
			moving_to_world[axis] = line->from[axis] + delta * travel;
		}
		int from[3] = {};
		int to[3] = {};
		if (!ProjectCoords(r, moving_from_world, from) || !ProjectCoords(r, moving_to_world, to))
			continue;
		if ((from[0] < 0 && to[0] < 0) || (from[0] >= width && to[0] >= width) ||
			(from[1] < 0 && to[1] < 0) || (from[1] >= height && to[1] >= height))
		{
			continue;
		}
		float fade = 1.0f;
		if (stamp >= line->spawn_stamp)
		{
			fade = elapsed >= 450000 ? 0.0f : 1.0f - (float)elapsed / 450000.0f;
		}
		if (fade <= 0.0f)
			continue;
		float from_vary[2] = { 231.0f, (float)'=' };
		float to_vary[2] = { fade > 0.45f ? 226.0f : 220.0f, (float)'-' };
		CellLine<Sample, 2, ProjectileLineCellWriter>(
			r->sample_buffer.ptr, ptr, width, height, from, to, '-', 226,
			from_vary, to_vary, ProjectileLineCellWriter());
	}
}
}

void Render(Renderer* r, uint64_t stamp, Terrain* t, World* w, float water, float zoom, float yaw, const float pos[3], const float lt[4], int width, int height, AnsiCell* ptr, Inst* inst, const int scene_shift[2], bool perspective, const RenderFrameInput* frame_input, RenderFrameReport* out_report)
{
	r->frame_input = frame_input;
	memset(&r->queue_report, 0, sizeof(r->queue_report));
	memset(&r->actor_report, 0, sizeof(r->actor_report));
	memset(&r->material_glyph_report, 0, sizeof(r->material_glyph_report));
	if (out_report)
		memset(out_report, 0, sizeof(*out_report));
	r->perspective = perspective;

	AnsiCell* out_ptr = ptr;

	double dt = (double)(stamp - r->stamp);
	r->stamp = stamp;
	r->pn_time += 0.02 * dt / 16666.0; // dt is in microsecs
	if (r->pn_time >= 1000000000000.0)
		r->pn_time = 0.0;


#ifdef DBL
	float scale = 3.0;
#else
	float scale = 1.5;
#endif

	zoom *= scale;

#ifdef DBL
	int dw = 4+2*width;
	int dh = 4+2*height;
#else
	int dw = 1 + width + 1;
	int dh = 1 + height + 1;
#endif

	float ds = 2*zoom / VISUAL_CELLS;

	if (!r->sample_buffer.ptr)
	{
		r->int_flag = true;
		for (int uv=0; uv<HEIGHT_CELLS; uv++)
		{
			r->patch_uv[uv][0] = uv * VISUAL_CELLS / HEIGHT_CELLS;
			r->patch_uv[uv][1] = (uv+1) * VISUAL_CELLS / HEIGHT_CELLS;
		};


		r->sample_buffer.w = dw;
		r->sample_buffer.h = dh;
		// Hidden work buffer: the terminal only shows width*height cells, but the
		// renderer first draws into a 2x supersampled Sample grid for cleaner
		// edges and better sprite/material resolution.
		r->sample_buffer.ptr = (Sample*)malloc(dw*dh * sizeof(Sample) * 2); // upper half is clear cache

		for (int cl = dw * dh; cl < 2*dw*dh; cl++)
		{
			r->sample_buffer.ptr[cl].height = -1000000;
			r->sample_buffer.ptr[cl].spare = 0x8;
			r->sample_buffer.ptr[cl].diffuse = 0xFF;
			r->sample_buffer.ptr[cl].visual = 0xC | (0xC << 5) | (0x1B << 10);
		}
	}
	else
	if (r->sample_buffer.w != dw || r->sample_buffer.h != dh)
	{
		r->int_flag = true;
		r->sample_buffer.w = dw;
		r->sample_buffer.h = dh;
		free(r->sample_buffer.ptr);
		r->sample_buffer.ptr = (Sample*)malloc(dw*dh * sizeof(Sample) * 2); // upper half is clear cache

		for (int cl = dw * dh; cl < 2 * dw*dh; cl++)
		{
			r->sample_buffer.ptr[cl].height = -1000000;
			r->sample_buffer.ptr[cl].spare = 0x8;
			r->sample_buffer.ptr[cl].diffuse = 0xFF;
			r->sample_buffer.ptr[cl].visual = 0xC | (0xC << 5) | (0x1B << 10);
		}
	}
	else
	{
		if (pos[0] != r->pos[0] || pos[1] != r->pos[1] || pos[2] != r->pos[2])
		{
			r->int_flag = true;
		}

		if (yaw != r->yaw)
		{
			r->int_flag = false;
		}
	}

	if (r->perspective) // #if PERSPECTIVE_TEST
	{
		r->int_flag = false;
	} // #endif

	r->pos[0] = pos[0];
	r->pos[1] = pos[1];
	r->pos[2] = pos[2];
	r->yaw = yaw;

	r->light[0] = lt[0];
	r->light[1] = lt[1];
	r->light[2] = lt[2];
	r->light[3] = lt[3];

	// memset(r->sample_buffer.ptr, 0x00, dw*dh * sizeof(Sample));
	// [FLOW:RENDER] Stage 1: Clear — reset SampleBuffer from cached clean state
	// WHY: memcpy from the pre-initialized upper half of the allocation is
	// faster than per-element memset. The clean state has height=-1000000
	// (guaranteed depth fail), spare=0x8, diffuse=0xFF, and a sky-blue visual.
	// Copy a prebuilt "empty frame template" into the active SampleBuffer.
	memcpy(r->sample_buffer.ptr, r->sample_buffer.ptr + dw * dh, dw*dh * sizeof(Sample));

	// for every cell we need to know world's xy coord where z is at the water level


	static const double sin30 = sin(M_PI*30.0/180.0); 
	static const double cos30 = cos(M_PI*30.0/180.0);

	/*
	static int frame = 0;
	frame++;
	if (frame == 200)
		frame = 0;
	water += HEIGHT_SCALE * 5 * sinf(frame*M_PI*0.01);
	*/

	// water integerificator (there's 4 instead of 2 because reflection goes 2x faster than water)
	int water_i = (int)floor(water / (HEIGHT_SCALE / (4 * ds * cos30)));
	water = (float)(water_i * (HEIGHT_SCALE / (4 * ds * cos30)));

	r->water = water;

	double a = yaw * M_PI / 180.0;
	double sinyaw = sin(a);
	double cosyaw = cos(a);

	double tm[16];
	tm[0] = +cosyaw *ds;
	tm[1] = -sinyaw * sin30*ds;
	tm[2] = 0;
	tm[3] = 0;
	tm[4] = +sinyaw * ds;
	tm[5] = +cosyaw * sin30*ds;
	tm[6] = 0;
	tm[7] = 0;
	tm[8] = 0;
	tm[9] = +cos30/HEIGHT_SCALE*ds*HEIGHT_CELLS;
	tm[10] = 1.0; //+2./0xffff;
	tm[11] = 0;
	//tm[12] = dw*0.5 - (pos[0] * tm[0] + pos[1] * tm[4] + pos[2] * tm[8]) * HEIGHT_CELLS;
	//tm[13] = dh*0.5 - (pos[0] * tm[1] + pos[1] * tm[5] + pos[2] * tm[9]) * HEIGHT_CELLS;
	tm[12] = dw*0.5 - (pos[0] * tm[0] * HEIGHT_CELLS + pos[1] * tm[4] * HEIGHT_CELLS + pos[2] * tm[8]) + scene_shift[0]*2;
	tm[13] = dh*0.5 - (pos[0] * tm[1] * HEIGHT_CELLS + pos[1] * tm[5] * HEIGHT_CELLS + pos[2] * tm[9]) + scene_shift[1]*2;
	tm[14] = 0.0; //-1.0;
	tm[15] = 1.0;

	r->mul[0] = tm[0];
	r->mul[1] = tm[1];
	r->mul[2] = tm[4];
	r->mul[3] = tm[5];
	r->mul[4] = 0;
	r->mul[5] = tm[9];

	// if yaw didn't change, make it INTEGRAL (and EVEN in case of DBL)
	r->add[0] = tm[12];
	r->add[1] = tm[13] + 0.5;
	r->add[2] = tm[14];

	if (r->int_flag)
	{
		int x = (int)floor(r->add[0] + 0.5);
		int y = (int)floor(r->add[1] + 0.5);

		#ifdef DBL
		x &= ~1;
		y &= ~1;
		#endif

		r->add[0] = (double)x;
		r->add[1] = (double)y;
	}

	double proj_tm[] = { r->mul[0], r->mul[1], r->mul[2], r->mul[3], r->mul[4], r->mul[5], r->add[0], r->add[1], r->add[2] };

	int planes = 5;
	int view_flags = 0xAA; // should contain only bits that face viewing direction

	// sin/cos 30 are commented out to achieve 'architectural' perspective
	// (all vertical lines in world space remain vertical and parallel on screen)
	r->focal = (float)fmax(dw,dh) * 2.0f; //500;
	r->view_dir[0] = (float)( - sinyaw * 1); // cos30;
	r->view_dir[1] = (float)(cosyaw * 1); // cos30;
	r->view_dir[2] = 0.0f; // -sin30;

	r->view_pos[0] = HEIGHT_CELLS * pos[0] - r->view_dir[0] * r->focal;
	r->view_pos[1] = HEIGHT_CELLS * pos[1] - r->view_dir[1] * r->focal;
	r->view_pos[2] = pos[2];
	r->view_dir[0] /= r->focal;
	r->view_dir[1] /= r->focal;
	r->view_ofs[0] = (float)(dw/2 + scene_shift[0]*2);
	r->view_ofs[1] = (float)(dh/2 + scene_shift[1]*2);


	double clip_world[5][4];

	/*
	double clip_left[4] =   { 1, 0, 0, 1-0.2 };
	double clip_right[4] =  {-1, 0, 0, 1-0.2 };
	double clip_bottom[4] = { 0, 1, 0, 1-0.2 };
	double clip_top[4] =    { 0,-1, 0, 1-0.2 }; // +1 for prespective
	*/
	double clip_left[4] =   { 1, 0, 0, 1 };
	double clip_right[4] =  {-1, 0, 0, 1 };
	double clip_bottom[4] = { 0, 1, 0, 1 };
	double clip_top[4] =    { 0,-1, 0, 1 }; // +1 for prespective
	
	double clip_water[4] =  { 0, 0, 1, -((r->water-1)*2.0/0xffff - 1.0) };

	double world_corner[2][4][4];
	double* corner_ll = world_corner[0][0];
	double* corner_lr = world_corner[0][1];
	double* corner_ul = world_corner[0][2];
	double* corner_ur = world_corner[0][3];
	double focus_node[3] = 
	{
		pos[0] + sinyaw * r->focal / HEIGHT_CELLS,
		pos[1] - cosyaw * r->focal / HEIGHT_CELLS,
		pos[2] + sin30 * r->focal / HEIGHT_CELLS * HEIGHT_SCALE
	};


	if (r->perspective) // #if PERSPECTIVE_TEST
	{
		double neutral_plane[4] =
		{
			-sinyaw,
			cosyaw,
			0,
			sinyaw*pos[0] - cosyaw*pos[1]
		};

		double test = 0;
		double screen_corner[2][4][4]=
		{
			{
				{0+test,0+test,0,1},
				{dw-test,0+test,0,1},
				{0+test,dh-test,0,1},
				{dw-test,dh-test,0,1}
			},
			{
				{0+test,0+test,10,1},
				{dw-test,0+test,10,1},
				{0+test,dh-test,10,1},
				{dw-test,dh-test,10,1}
			}
		};

		double clip_tm[16];
		Invert(tm,clip_tm);

		for (int c=0; c<4; c++)
		{
			// transform corners from screen to premultiplied world
			Product(clip_tm, screen_corner[0][c], world_corner[0][c]);
			Product(clip_tm, screen_corner[1][c], world_corner[1][c]);

			// from premultiplied to world
			world_corner[0][c][0] /= HEIGHT_CELLS;
			world_corner[0][c][1] /= HEIGHT_CELLS;
			world_corner[1][c][0] /= HEIGHT_CELLS;
			world_corner[1][c][1] /= HEIGHT_CELLS;

			// intersect resulting corner lines with neutral_plane
			world_corner[1][c][0] -= world_corner[0][c][0];
			world_corner[1][c][1] -= world_corner[0][c][1];
			world_corner[1][c][2] -= world_corner[0][c][2];
			double a = -(DotProduct(neutral_plane,world_corner[0][c]) + neutral_plane[3])/DotProduct(neutral_plane, world_corner[1][c]);
			world_corner[0][c][0] += a * world_corner[1][c][0];
			world_corner[0][c][1] += a * world_corner[1][c][1];
			world_corner[0][c][2] += a * world_corner[1][c][2];
		}

		// note: for reflected planes, simply reflect corners and focal node ( z' = 2*water-z )

		// left  ( focus, ll, ul )
		PlaneFromPoints(focus_node, corner_ll, corner_ul, clip_world[0]);

		// right ( focus, ur, lr )
		PlaneFromPoints(focus_node, corner_ur, corner_lr, clip_world[1]);

		// top   ( focus, ul, ur )
		PlaneFromPoints(focus_node, corner_ul, corner_ur, clip_world[2]);

		// bottom( focus, lr, ll )
		PlaneFromPoints(focus_node, corner_lr, corner_ll, clip_world[3]);

		// water
		clip_world[4][0]=0;
		clip_world[4][1]=0;
		clip_world[4][2]=1;
		clip_world[4][3]=-clip_world[0][2]*(r->water-1);
	}
	else // #else
	// easier to use another transform for clipping
	{
		// somehow it works
		double clip_tm[16];
		clip_tm[0] = +cosyaw / (0.5 * dw) * ds * HEIGHT_CELLS;
		clip_tm[1] = -sinyaw*sin30 / (0.5 * dh) * ds * HEIGHT_CELLS;
		clip_tm[2] = 0;
		clip_tm[3] = 0;
		clip_tm[4] = +sinyaw / (0.5 * dw) * ds * HEIGHT_CELLS;
		clip_tm[5] = +cosyaw*sin30 / (0.5 * dh) * ds * HEIGHT_CELLS;
		clip_tm[6] = 0;
		clip_tm[7] = 0;
		clip_tm[8] = 0;
		clip_tm[9] = +cos30 / HEIGHT_SCALE / (0.5 * dh) * ds * HEIGHT_CELLS;
		clip_tm[10] = +2. / 0xffff;
		clip_tm[11] = 0;
		clip_tm[12] = -(pos[0] * clip_tm[0] + pos[1] * clip_tm[4] + pos[2] * clip_tm[8] - (double)scene_shift[0]*2/width );
		clip_tm[13] = -(pos[0] * clip_tm[1] + pos[1] * clip_tm[5] + pos[2] * clip_tm[9] - (double)scene_shift[1]*2/height);
		clip_tm[14] = -1.0;
		clip_tm[15] = 1.0;

		TransposeProduct(clip_tm, clip_left, clip_world[0]);
		TransposeProduct(clip_tm, clip_right, clip_world[1]);
		TransposeProduct(clip_tm, clip_bottom, clip_world[2]);
		TransposeProduct(clip_tm, clip_top, clip_world[3]);
		TransposeProduct(clip_tm, clip_water, clip_world[4]);
	}
	// #endif

	r->sprites = 0;
	// [FLOW:RENDER] Stage 2: Terrain — rasterize visible terrain patches
	// WHY: Terrain is rendered first because it forms the "ground truth"
	// depth baseline. Everything else (meshes, sprites, shadow) depth-tests
	// against the terrain heights in the SampleBuffer.

	// [FLOW:RENDER] Stage 3: World — query sprites and meshes via QueryWorld
	// WHY: After terrain fills the base landscape, world objects (3D meshes
	// and sprite instances) are queried. Meshes rasterize directly into the
	// SampleBuffer; sprites are deferred into SpriteRenderBuf for post-resolve blit.
	// Terrain establishes the first winning depths in the hidden SampleBuffer.
	QueryTerrain(t, planes, clip_world, view_flags, Renderer::RenderPatch, r);
	if (r->frame_input && r->frame_input->debug_hooks.after_terrain_stage)
		r->frame_input->debug_hooks.after_terrain_stage(r, r->frame_input->debug_hooks.user);
	QueryWorldCB cb = { Renderer::RenderMesh , Renderer::RenderSprite };
	// World meshes compete against terrain depth immediately; sprites are only
	// queued here and composited later after the terrain image is resolved.
	QueryWorld(w, planes, clip_world, &cb, r);

	// [FLOW:RENDER] Stage 4: Shadow — player blob shadow on terrain
	// WHY: The player shadow is projected onto the SampleBuffer by inverse-
	// transforming each nearby sample back to world space, computing distance
	// to the player position, and attenuating diffuse within a radius of ~2
	// world units. This runs AFTER terrain+world so the shadow falls on top.
	Invert(tm, r->inv_tm);
	Material* matlib = (Material*)GetMaterialArr();
	RenderStageShadow(r, dw, dh, width, pos, scene_shift, matlib);

	////////////////////
	// [FLOW:RENDER] Stage 5: Reflection — mirror geometry below water plane
	// WHY: Water reflections are achieved by flipping the Z axis in the view
	// matrix (Z' = 2*water - Z) and re-querying terrain+world with
	// global_refl_mode=true. The reflected geometry writes into the SAME
	// SampleBuffer with spare|=0x3 (reflection parity). During resolve,
	// reflected samples get dimmed and blended with non-reflected terrain.

	// once again for reflections
	tm[8] = -tm[8];
	tm[9] = -tm[9];
	tm[10] = -tm[10]; // let them simply go below 0 :)

	//tm[12] = dw*0.5 - (pos[0] * tm[0] + pos[1] * tm[4] + ((2 * water / HEIGHT_CELLS) - pos[2]) * tm[8]) * HEIGHT_CELLS;
	//tm[13] = dh*0.5 - (pos[0] * tm[1] + pos[1] * tm[5] + ((2 * water / HEIGHT_CELLS) - pos[2]) * tm[9]) * HEIGHT_CELLS;
	tm[12] = dw*0.5 - (pos[0] * tm[0] * HEIGHT_CELLS + pos[1] * tm[4] * HEIGHT_CELLS + ((2 * water) - pos[2]) * tm[8]) + scene_shift[0]*2;
	tm[13] = dh*0.5 - (pos[0] * tm[1] * HEIGHT_CELLS + pos[1] * tm[5] * HEIGHT_CELLS + ((2 * water) - pos[2]) * tm[9]) + scene_shift[1]*2;
	tm[14] = 2*r->water;

	r->mul[0] = tm[0];
	r->mul[1] = tm[1];
	r->mul[2] = tm[4];
	r->mul[3] = tm[5];
	r->mul[4] = 0;
	r->mul[5] = tm[9];

	// if yaw didn't change, make it INTEGRAL (and EVEN in case of DBL)
	r->add[0] = tm[12];
	r->add[1] = tm[13] + 0.5;
	r->add[2] = tm[14];

	if (r->int_flag)
	{
		int x = (int)floor(r->add[0] + 0.5);
		int y = (int)floor(r->add[1] + 0.5);

		#ifdef DBL
		x &= ~1;
		y &= ~1;
		#endif

		r->add[0] = (double)x;
		r->add[1] = (double)y;
	}

	double refl_tm[] = { r->mul[0], r->mul[1], r->mul[2], r->mul[3], r->mul[4], r->mul[5], r->add[0], r->add[1], r->add[2] };

	if (r->perspective) // #if PERSPECTIVE_TEST
	{
		corner_ll[2] = 2*water - corner_ll[2];
		corner_lr[2] = 2*water - corner_lr[2];
		corner_ul[2] = 2*water - corner_ul[2];
		corner_ur[2] = 2*water - corner_ur[2];

		focus_node[2] = 2*water - focus_node[2];
		
		// left  ( focus, ll, ul )
		PlaneFromPoints(focus_node, corner_ul, corner_ll, clip_world[0]);

		// right ( focus, ur, lr )
		PlaneFromPoints(focus_node, corner_lr, corner_ur, clip_world[1]);

		// top   ( focus, ul, ur )
		PlaneFromPoints(focus_node, corner_ur, corner_ul, clip_world[2]);

		// bottom( focus, lr, ll )
		PlaneFromPoints(focus_node, corner_ll, corner_lr, clip_world[3]);	

		clip_world[4][0]=0;
		clip_world[4][1]=0;
		clip_world[4][2]=1; // note: during refl, we again query ABOVE water!
		clip_world[4][3]=-clip_world[0][2]*(r->water-1);
	}
	else // #else
	{
		clip_water[2] = -1; // was +1
		clip_water[3] = +((r->water+1)*-2.0 / 0xffff + 1.0); // was -((r->water-1)*2.0/0xffff - 1.0)
	
		// somehow it works
		double clip_tm[16];
		clip_tm[0] = +cosyaw / (0.5 * dw) * ds * HEIGHT_CELLS;
		clip_tm[1] = -sinyaw * sin30 / (0.5 * dh) * ds * HEIGHT_CELLS;
		clip_tm[2] = 0;
		clip_tm[3] = 0;
		clip_tm[4] = +sinyaw / (0.5 * dw) * ds * HEIGHT_CELLS;
		clip_tm[5] = +cosyaw * sin30 / (0.5 * dh) * ds * HEIGHT_CELLS;
		clip_tm[6] = 0;
		clip_tm[7] = 0;
		clip_tm[8] = 0;
		clip_tm[9] = -cos30 / HEIGHT_SCALE / (0.5 * dh) * ds * HEIGHT_CELLS;
		clip_tm[10] = -2. / 0xffff;
		clip_tm[11] = 0;
		clip_tm[12] = -(pos[0] * clip_tm[0] + pos[1] * clip_tm[4] + (2 * r->water - pos[2]) * clip_tm[8] - (double)scene_shift[0] * 2 / width);
		clip_tm[13] = -(pos[0] * clip_tm[1] + pos[1] * clip_tm[5] + (2 * r->water - pos[2]) * clip_tm[9] - (double)scene_shift[1] * 2 / height);
		clip_tm[14] = +1.0;
		clip_tm[15] = 1.0;

		TransposeProduct(clip_tm, clip_left, clip_world[0]);
		TransposeProduct(clip_tm, clip_right, clip_world[1]);
		TransposeProduct(clip_tm, clip_bottom, clip_world[2]);
		TransposeProduct(clip_tm, clip_top, clip_world[3]);
		TransposeProduct(clip_tm, clip_water, clip_world[4]);
	}
	// #endif

	// Reflection is just a second mirrored terrain/world pass into the same
	// SampleBuffer, tagged so resolve can dim/blend it differently.
	global_refl_mode = true;
	QueryTerrain(t, planes, clip_world, view_flags, Renderer::RenderPatch, r);
	QueryWorld(w, planes, clip_world, &cb, r);

	global_refl_mode = false;
	if (r->frame_input && r->frame_input->debug_hooks.after_reflection_stage)
		r->frame_input->debug_hooks.after_reflection_stage(r, r->frame_input->debug_hooks.user);

	// clear and write new water ripples from player history position
	// do not emit wave if given z is greater than water level!
	memset(out_ptr, 0, sizeof(AnsiCell)*width*height);

	/*
	for (int h = 0; h < 64; h++)
	{
		float* xyz = hist[h];
		if (xyz[2] < water)
		{
			// draw ellipse
		}
	}
	*/

	RenderStageResolve(r, ptr, width, height, matlib, r->inv_tm, water);

render_sort_and_blit:

	r->mul[0] = proj_tm[0];
	r->mul[1] = proj_tm[1];
	r->mul[2] = proj_tm[2];
	r->mul[3] = proj_tm[3];
	r->mul[4] = proj_tm[4];
	r->mul[5] = proj_tm[5];
	r->add[0] = proj_tm[6];
	r->add[1] = proj_tm[7];
	r->add[2] = proj_tm[8];
	RenderProjectileLines(r, out_ptr, width, height, stamp);

	// TODO(PIPELINE-FIX): Far-to-near sort assumes all sprites have correct
	// depth values from the projection step. If sprite world positions come
	// from .xp metadata with incorrect Z offsets, sort order will be wrong
	// and sprites will render in front of/behind incorrect geometry.
	// Deferred sprites are composited after terrain/mesh resolve, but still use
	// the hidden SampleBuffer depth so bodies obey world occlusion.
	qsort(r->sprites_alloc, r->sprites, sizeof(SpriteRenderBuf), SpriteRenderBuf::FarToNear);

	// lets check drawing sprites in world space
	// S4/FL-1434: render-side pre-blit floor clamp and post-blit support retry were
	// deleted 2026-04-23 (a04bba3d) as Law 1/Law 2 violations. They were temporarily
	// restored (87b13717) and confirmed to cause Law 1 mixed ownership (FL-1326).
	// Do NOT re-add QuerySpriteCenterSupportHeight, the pre-blit s_pos[2] write,
	// or the corpse/character support retry block here.
	// The dbg_remote0_blit_clamp_* fields below are diagnostic probes ONLY.
	const int tracked_remote_pid = (r->frame_input && r->frame_input->valid) ? r->frame_input->tracking.tracked_remote_pid : -1;
	for (int s=0; s<r->sprites; s++)
	{
		SpriteRenderBuf* buf = r->sprites_alloc + s;

		if (!buf->refl && buf->tracked_remote && out_report)
			out_report->remote_clamp = BuildTrackedRemoteClampReportForSprite(
				r->sample_buffer, buf, width, height);
		// IT IS PERFECTLY STICKED TO WORLD!
		// it may not perectly stick to character but its fine! (its kinda character is not perfectly positioned)

		// todo: use buf->alpha (perspective fades)

		int frame = buf->frame;
		int anim = buf->anim;
		if (!buf->refl && buf->character && out_report)
		{
			if (buf->remote_pid >= 0)
			{
				out_report->remote_blit.last_remote_blit_pid = buf->remote_pid;
				out_report->remote_blit.last_remote_blit_matches_head =
					(tracked_remote_pid >= 0 && buf->remote_pid == tracked_remote_pid) ? 1 : 0;
			}
			if (out_report->remote_blit.last_remote_blit_matches_head)
				out_report->remote_blit.final_blit_attempted = 1;
			if (buf->tracked_remote)
			{
				out_report->remote_blit.tracked_buf_blit_invoked = 1;
				out_report->remote_clamp.blit_post_clamp_rewrite =
					(fabsf(out_report->remote_clamp.blit_post_clamp_s_pos_z - (float)buf->s_pos[2]) > 0.5f) ? 1 : 0;
				out_report->remote_clamp.blit_s_pos_z = (float)buf->s_pos[2];
			}
		}
		if (buf->tracked_npc && out_report)
			out_report->tracked_npc.body_blit_attempted = 1;
		// S4/FL-1434: the 88-line corpse/character support retry block that
		// previously followed this call is permanently deleted. Server owns Z.
		// Do not add a second RenderSprite call, a lift_z retry, or a
		// fallback_floor_z path here. See U3/FL-2023 for the open server-side fix.
		SpriteBlitDiagnostics diag = r->RenderSprite(out_ptr, width, height,
			buf->sprite, buf->refl, anim, frame, buf->angle, buf->s_pos);
		if (out_report)
		{
			if (!buf->refl && buf->tracked_remote)
				FillTrackedRemoteBlitReport(buf, diag, &out_report->remote_blit);
			if (!buf->refl && buf->tracked_npc)
				FillTrackedNpcRenderReport(buf, diag, &out_report->tracked_npc);
			if (!buf->refl && buf->is_local_player)
			{
				r->actor_report.sprite_row_seen = 1;
				r->actor_report.sprite_angle = buf->angle;
				r->actor_report.sprite_angles = buf->sprite ? buf->sprite->angles : 0;
				r->actor_report.sprite_anim = anim;
				r->actor_report.sprite_frame = frame;
				// FL-4079: capture body screen anchor + clip rect for wearable proof probe
				r->actor_report.body_screen_pos_x = diag.screen_pos_x;
				r->actor_report.body_screen_pos_y = diag.screen_pos_y;
				r->actor_report.body_ref_x = diag.ref_x;
				r->actor_report.body_ref_y = diag.ref_y;
				r->actor_report.body_frame_width = diag.frame_width;
				r->actor_report.body_frame_height = diag.frame_height;
				r->actor_report.body_clip_left = diag.clip_left;
				r->actor_report.body_clip_right = diag.clip_right;
				r->actor_report.body_clip_bottom = diag.clip_bottom;
				r->actor_report.body_clip_top = diag.clip_top;
			}
			if (!buf->refl && buf->is_local_player && diag.drew_any)
				out_report->actor.actor_final_body_drawn = 1;
		}
	}

	if (out_report)
	{
		out_report->queue = r->queue_report;
		out_report->material_glyph = r->material_glyph_report;
		if (r->actor_report.sprite_row_seen)
		{
			out_report->actor.sprite_row_seen = r->actor_report.sprite_row_seen;
			out_report->actor.sprite_angle = r->actor_report.sprite_angle;
			out_report->actor.sprite_angles = r->actor_report.sprite_angles;
			out_report->actor.sprite_anim = r->actor_report.sprite_anim;
			out_report->actor.sprite_frame = r->actor_report.sprite_frame;
			// FL-4079
			out_report->actor.body_screen_pos_x = r->actor_report.body_screen_pos_x;
			out_report->actor.body_screen_pos_y = r->actor_report.body_screen_pos_y;
			out_report->actor.body_ref_x = r->actor_report.body_ref_x;
			out_report->actor.body_ref_y = r->actor_report.body_ref_y;
			out_report->actor.body_frame_width = r->actor_report.body_frame_width;
			out_report->actor.body_frame_height = r->actor_report.body_frame_height;
			out_report->actor.body_clip_left = r->actor_report.body_clip_left;
			out_report->actor.body_clip_right = r->actor_report.body_clip_right;
			out_report->actor.body_clip_bottom = r->actor_report.body_clip_bottom;
			out_report->actor.body_clip_top = r->actor_report.body_clip_top;
		}
	}

	RenderHpBars(r, out_ptr, width, height,
		out_report ? &out_report->tracked_npc : nullptr);

	// restore positive projection for ProjectCoords func (now they are for reflection).

	r->mul[0] = proj_tm[0];
	r->mul[1] = proj_tm[1];
	r->mul[2] = proj_tm[2];
	r->mul[3] = proj_tm[3];
	r->mul[4] = proj_tm[4];
	r->mul[5] = proj_tm[5];
	r->add[0] = proj_tm[6];
	r->add[1] = proj_tm[7];
	r->add[2] = proj_tm[8];

	
	/*
	int invpos[3] = { 1,1,0 };
	if (inventory_sprite)
		r->RenderSprite(out_ptr, width, height, inventory_sprite, false, 0, 0, 0, invpos);
	*/

}
