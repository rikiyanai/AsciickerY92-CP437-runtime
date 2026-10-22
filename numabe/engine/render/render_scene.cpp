// render_scene.cpp — Render() top-level frame function
//
// Extracted from engine/render.cpp.
// SEE ALSO: render.h, render_internal.h

#include "render_internal.h"
#include "renderer_ports_v1.h"
#include "world.h" // FL-4208 N0a: GetWorldGeneration
#include "matrix.h"
#include "render_stage_resolve.h"
#include "render_debug_observation.h"
#include "render_observation_builder.h"
#include "render_hud_overlay.h"
#include "render_stage_shadow.h"
#include "render_rasterize.h"
#include "render_s3.h"
#include "../rapture_timeline.h"
#include "../rapture_beam.h"
#include "../rapture_standoff.h"
#include "../world_s3.h"
#include "../game_utility.h"
#include <unordered_map>
#include <unordered_set>
#include <vector>

#ifdef DBL
static_assert(RAPTURE_SCREEN_COLS_PER_WORLD_UNIT ==
	(2.0f * SPRITE_ZOOM * 3.0f / VISUAL_CELLS) * HEIGHT_CELLS * 0.5f,
	"rapture footprint columns-per-world-unit mirror is stale");
static_assert(RAPTURE_SCREEN_ROWS_PER_WORLD_UNIT ==
	(2.0f * SPRITE_ZOOM * 3.0f / VISUAL_CELLS) * 0.5f * HEIGHT_CELLS * 0.5f,
	"rapture footprint rows-per-world-unit mirror is stale");
#endif

extern void* GetMaterialArr();

namespace
{

// FL-4208 N0a (presentation-neutral) — post-raster, pre-resolve identity pass.
// Iterates the same 2x2 final-sample block per terminal cell as the
// Current-owned pre-glyph scene builder, reconstructs each surviving terrain sample's full
// Fl4208SurfaceId from the provisional plane, resolves one represented surface
// by stable ownership, derives one cell anchor from that surface's surviving
// samples, interns the resulting cell identity once, and writes the shared
// index back only to those samples. Transient depth winners never enter the
// interner. Reads and writes only surface identity data; presentation consumes
// the resulting cell record through its separate temporal owner.
static void Fl4208ResolveCellIdentities(Renderer* r, int width, int height)
{
	if (!r || !r->sample_buffer.ptr || !r->fl4208_prov)
		return;
	const int dw = r->sample_buffer.w;
	const int sample_xy = 2 + 2 * dw;
	const int cap = r->fl4208_prov_cap;
	// FL-4208 C13: retain the FULL per-cell identity for the native bridge on every
	// frame. The sole identity producer remains this post-raster pass; the temporal
	// presentation owner added later consumes this retained grid, never screen-cell
	// history. Trace export is optional, identity availability is not.
	const bool isolated_observation = r->frame_input &&
		r->frame_input->debug_hooks.isolated_observation;
	const bool trace = g_fl4208_config.trace_enabled && !isolated_observation;
	const int cells = width * height;
	bool capture_invalid = false; // reviewer flag #3: fail-closed receipt state
	if (cells > 0 && r->fl4208_cell_cap != cells)
	{
		free(r->fl4208_cell_ids);
		free(r->fl4208_cell_state);
		free(r->fl4208_cell_reason);
		r->fl4208_cell_ids   = (Fl4208SurfaceId*)malloc(sizeof(Fl4208SurfaceId) * (size_t)cells);
		r->fl4208_cell_state = (uint8_t*)malloc((size_t)cells);
		r->fl4208_cell_reason = (uint8_t*)malloc((size_t)cells);
		r->fl4208_cell_cap   = (r->fl4208_cell_ids && r->fl4208_cell_state &&
		                        r->fl4208_cell_reason) ? cells : 0;
	}
	// A failed allocation denies temporal input for the frame. It never grants a
	// screen-position fallback and never changes the current presented GlyphId.
	capture_invalid = (cells > 0) && (!r->fl4208_cell_ids || !r->fl4208_cell_state ||
	                                  !r->fl4208_cell_reason || r->fl4208_cell_cap < cells);
	if (r->fl4208_cell_ids)   memset(r->fl4208_cell_ids, 0, sizeof(Fl4208SurfaceId) * (size_t)r->fl4208_cell_cap);
	if (r->fl4208_cell_state) memset(r->fl4208_cell_state, 0, (size_t)r->fl4208_cell_cap);
	if (r->fl4208_cell_reason) memset(r->fl4208_cell_reason, 0, (size_t)r->fl4208_cell_cap);

	// FL-4512 mesh_surface_id.v1 owner registry. This table retains the complete
	// tuple behind every 64-bit SHA truncation. A collision poisons the hash for
	// the whole frame; no farther surface may substitute for the colliding winner.
	struct MeshOwnerTuple { uint64_t uid; uint32_t primitive; };
	std::unordered_map<uint64_t, MeshOwnerTuple> mesh_owner_by_hash;
	std::unordered_set<uint64_t> mesh_owner_hash_collisions;
	for (int si = 0; si < cap; si++)
	{
		const Fl4208ProvisionalId& p = r->fl4208_prov[si];
		if (!p.valid || p.owner_kind != FL4208_OWNER_MESH ||
		    r->sample_buffer.ptr[si].glyph_dispatch_surface != GLYPH_DISPATCH_AUTOMAP_MESH_RGB)
			continue;
		const uint64_t owner_hash = (uint64_t)(uint32_t)p.owner_x |
			((uint64_t)(uint32_t)p.owner_y << 32);
		auto found = mesh_owner_by_hash.find(owner_hash);
		if (found == mesh_owner_by_hash.end())
			mesh_owner_by_hash.emplace(owner_hash, MeshOwnerTuple{p.mesh_instance_uid, p.primitive_id});
		else if (found->second.uid != p.mesh_instance_uid ||
		         found->second.primitive != p.primitive_id)
			mesh_owner_hash_collisions.insert(owner_hash);
	}

	std::vector<uint8_t> mesh_selected_tap((size_t)cells, 0xFF);
	for (int y = 0; y < height; y++)
	{
		for (int x = 0; x < width; x++)
		{
			const int base = sample_xy + 2 * x + (2 * y) * dw;
			const int idx4[4] = { base, base + 1, base + dw, base + dw + 1 };
			Fl4208CellInput in = {};
			Fl4208MeshCellInput mesh_in = {};
			bool has_mesh = false;
			bool has_sprite = false;
			for (int i = 0; i < 4; i++)
			{
				const int si = idx4[i];
				in.final_dispatch[i] = r->sample_buffer.ptr[si].glyph_dispatch_surface;
				mesh_in.final_dispatch[i] = in.final_dispatch[i];
				mesh_in.depth[i] = r->sample_buffer.ptr[si].height;
				// Terrain depth-winner fallback (FL-4208 residual burn-down)
				// consumes the same native sample depth the mesh rule uses.
				in.depth[i] = r->sample_buffer.ptr[si].height;
				has_mesh = has_mesh || in.final_dispatch[i] == GLYPH_DISPATCH_AUTOMAP_MESH_RGB;
				has_sprite = has_sprite || in.final_dispatch[i] == GLYPH_DISPATCH_SPRITE_ACTOR;
				if (si >= 0 && si < cap)
				{
					in.prov[i] = r->fl4208_prov[si];
					mesh_in.prov[i] = r->fl4208_prov[si];
					const uint64_t owner_hash = (uint64_t)(uint32_t)mesh_in.prov[i].owner_x |
						((uint64_t)(uint32_t)mesh_in.prov[i].owner_y << 32);
					mesh_in.owner_hash_collision[i] =
						mesh_owner_hash_collisions.find(owner_hash) != mesh_owner_hash_collisions.end();
				}
				else
				{
					memset(&in.prov[i], 0, sizeof(in.prov[i]));
					memset(&mesh_in.prov[i], 0, sizeof(mesh_in.prov[i]));
				}
			}
			Fl4208CellDecision d = {};
			d.selected_tap = 0xFF;
			if (!has_sprite)
			{
				d = has_mesh
					? Fl4208DecideMeshCell(mesh_in, (uint8_t)GLYPH_DISPATCH_AUTOMAP_MESH_RGB)
					: Fl4208DecideCell(in, (uint8_t)GLYPH_DISPATCH_TERRAIN_MATERIAL,
					                   r->fl4208_interner);
			}
			for (int i = 0; i < 4; i++)
				r->sample_buffer.ptr[idx4[i]].surface_id_index = d.index[i];
			const int ci = y * width + x;
			if (has_mesh)
				mesh_selected_tap[(size_t)ci] = d.selected_tap;
			if (r->fl4208_cell_ids && ci < r->fl4208_cell_cap)
			{
				r->fl4208_cell_ids[ci]   = d.id;
				r->fl4208_cell_state[ci] = d.rejected_conflict ? 2 : (d.cell_valid ? 1 : 0);
				r->fl4208_cell_reason[ci] = d.reason;
			}
		}
	}

	// A quantized complete SurfaceId may still land in two visible cells. The key
	// is temporal authority for both terrain and mesh, so classify collisions
	// only after every cell exists and invalidate every member before history.
	struct SurfaceIdHash {
		size_t operator()(const Fl4208SurfaceId& id) const {
			return (size_t)Fl4208IdentityInterner::Hash(id);
		}
	};
	std::unordered_map<Fl4208SurfaceId, int, SurfaceIdHash> first_history_cell;
	std::unordered_set<Fl4208SurfaceId, SurfaceIdHash> anchor_collisions;
	if (!capture_invalid)
	{
		for (int ci = 0; ci < cells; ci++)
		{
			if (r->fl4208_cell_state[ci] != 1 ||
				(r->fl4208_cell_ids[ci].owner_kind != FL4208_OWNER_TERRAIN &&
				 r->fl4208_cell_ids[ci].owner_kind != FL4208_OWNER_MESH))
				continue;
			auto inserted = first_history_cell.emplace(r->fl4208_cell_ids[ci], ci);
			if (!inserted.second)
				anchor_collisions.insert(r->fl4208_cell_ids[ci]);
		}
		for (int ci = 0; ci < cells; ci++)
		{
			if (r->fl4208_cell_state[ci] == 1 &&
				anchor_collisions.find(r->fl4208_cell_ids[ci]) !=
					anchor_collisions.end())
			{
				r->fl4208_cell_state[ci] = 2;
				r->fl4208_cell_reason[ci] =
					r->fl4208_cell_ids[ci].owner_kind == FL4208_OWNER_MESH
						? FL4208_CELL_ID_REASON_MESH_ANCHOR_COLLISION
						: FL4208_CELL_ID_REASON_TERRAIN_ANCHOR_COLLISION;
				const int x = ci % width;
				const int y = ci / width;
				const int base = sample_xy + 2 * x + (2 * y) * dw;
				const int idx4[4] = { base, base + 1, base + dw, base + dw + 1 };
				for (int tap = 0; tap < 4; ++tap)
					r->sample_buffer.ptr[idx4[tap]].surface_id_index =
						Fl4208IdentityInterner::INVALID_INDEX;
			}
		}
		for (int ci = 0; ci < cells; ci++)
		{
			if (r->fl4208_cell_state[ci] != 1 ||
			    r->fl4208_cell_ids[ci].owner_kind != FL4208_OWNER_MESH)
				continue;
			const uint32_t index = r->fl4208_interner.Intern(r->fl4208_cell_ids[ci]);
			const uint8_t tap = mesh_selected_tap[(size_t)ci];
			if (tap < 4)
			{
				const int x = ci % width, y = ci / width;
				const int base = sample_xy + 2 * x + (2 * y) * dw;
				const int idx4[4] = { base, base + 1, base + dw, base + dw + 1 };
				r->sample_buffer.ptr[idx4[tap]].surface_id_index = index;
			}
		}
	}

	if (trace && !capture_invalid)
	{
		for (int ci = 0; ci < cells; ci++)
		{
			if (r->fl4208_cell_state[ci] == 1)
				r->fl4208_trace.cells_valid++;
			else if (r->fl4208_cell_state[ci] == 2)
				r->fl4208_trace.cells_rejected++;
			if (r->fl4208_cell_reason[ci] == FL4208_CELL_ID_REASON_MESH_OWNER_HASH_COLLISION)
				r->fl4208_trace.mesh_owner_hash_collision_cells++;
			if (r->fl4208_cell_reason[ci] == FL4208_CELL_ID_REASON_MESH_ANCHOR_COLLISION)
				r->fl4208_trace.mesh_anchor_collision_cells++;
			if (r->fl4208_cell_reason[ci] ==
				FL4208_CELL_ID_REASON_TERRAIN_ANCHOR_COLLISION)
				r->fl4208_trace.terrain_anchor_collision_cells++;
		}
	}
	if (trace)
	{
		r->fl4208_trace.records  = r->fl4208_interner.record_count;
		r->fl4208_trace.overflow = r->fl4208_interner.overflowed ? 1 : 0;
	}

	// reviewer flag #2/#3: export a versioned per-frame receipt so the N0b sweep
	// can compare runs from disk; a capture-allocation failure writes a
	// status=INVALID marker (cell_count 0) instead of an empty terrain frame, and
	// an export OPEN failure writes no receipt at all (N0b reads the gap as fail,
	// never as a clean empty run).
	const uint64_t frame = r->fl4208_frame++;
	// FL-4208 N2.0 (reviewer fix): record the PRE-increment value as this grid's
	// capture frame. r->fl4208_frame is now the NEXT frame; downstream consumers
	// (the N2.0 bridge copy in RenderStageResolve) must read fl4208_capture_frame,
	// which matches the frame number this receipt is written under just below.
	r->fl4208_capture_frame = frame;
	if (trace && g_fl4208_config.trace_path_set)
	{
		// reviewer flag #6: the FIRST frame of this process truncates so a fresh
		// run never appends onto a stale receipt (duplicate frame numbers); later
		// frames append. The reader additionally rejects duplicates/gaps.
		FILE* tf = fopen(g_fl4208_config.trace_path, frame == 0 ? "wb" : "ab");
		if (!tf)
		{
			fprintf(stderr, "[FL-4208] trace export open failed: %s\n", g_fl4208_config.trace_path);
		}
		else
		{
			const uint32_t status = capture_invalid ? (uint32_t)FL4208_TRACE_INVALID : (uint32_t)FL4208_TRACE_OK;
			long wrote = Fl4208WriteTraceFrame(tf, status, frame, r->stamp,
				(uint32_t)width, (uint32_t)height, g_fl4208_config.anchor_bins,
				FL4208_MESH_ANCHOR_BINS_V1,
				r->fl4208_trace, r->fl4208_cell_ids, r->fl4208_cell_state,
				r->fl4208_cell_reason,
				capture_invalid ? 0u : (uint32_t)cells,
				g_fl4208_config.capture_spec_sha256);
			if (wrote < 0)
				fprintf(stderr, "[FL-4208] trace export write failed: %s\n", g_fl4208_config.trace_path);
			// reviewer flag #4 (round 4): a buffered write can surface its error
			// only at flush/close, so fclose is checked too.
			if (fclose(tf) != 0)
				fprintf(stderr, "[FL-4208] trace export close failed: %s\n", g_fl4208_config.trace_path);
		}
	}
}

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

static std::vector<AnsiCell> g_rapture_pre_actor;
static int g_rapture_pre_actor_w = 0;
static int g_rapture_pre_actor_h = 0;

static bool RaptureAnyBeam(const Renderer* r)
{
	if (!r || !r->frame_input)
		return false;
	const RenderFrameInput* input = r->frame_input;
	for (int i = 0; i < input->rapture_count && i < RENDER_MAX_RAPTURES; i++)
		if (input->raptures[i].active &&
			RaptureEval(input->raptures[i].ticks_since_death).beam_len01 > 0.0f)
			return true;
	return false;
}

static void RaptureSnapshotPreActor(Renderer* r, const AnsiCell* ptr, int width, int height)
{
	g_rapture_pre_actor_w = g_rapture_pre_actor_h = 0;
	if (!ptr || width <= 0 || height <= 0 || !RaptureAnyBeam(r))
		return;
	g_rapture_pre_actor.assign(ptr, ptr + (size_t)width * (size_t)height);
	g_rapture_pre_actor_w = width;
	g_rapture_pre_actor_h = height;
}

// Depth-tested deterministic cone and footprint. It reads depth but does not
// become a world/physics owner; sprite cells keep their glyph and are tinted.
static void RenderRaptureBeams(Renderer* r, AnsiCell* ptr, int width, int height)
{
	if (!r || !r->frame_input || !ptr || !r->sample_buffer.ptr)
		return;
	const RenderFrameInput* input = r->frame_input;
	Sample* smp = r->sample_buffer.ptr;
	const bool have_snapshot =
		g_rapture_pre_actor_w == width && g_rapture_pre_actor_h == height;
	const bool hull_shadow = RAPTURE_UFO_VISIBLE && input->ufo_sprite != nullptr;
	for (int i = 0; i < input->rapture_count && i < RENDER_MAX_RAPTURES; i++)
	{
		const RenderRapture* rr = &input->raptures[i];
		if (!rr->active)
			continue;
		const RapturePhase phase = RaptureEval(rr->ticks_since_death);
		if (!(phase.beam_len01 > 0.0f))
			continue;
		const float hull_world[3] = {
			rr->ground[0], rr->ground[1], RaptureUfoZ(rr->ground[2], phase)};
		int ground_view[3] = {};
		int hull_view[3] = {};
		if (!ProjectCoords(r, rr->ground, ground_view) ||
			!ProjectCoords(r, hull_world, hull_view))
			continue;
		const int beam_rows = hull_view[1] - ground_view[1];
		if (beam_rows < 2)
			continue;
		const int axis_x = ground_view[0];
		const int max_hw = (int)RAPTURE_BEAM_RING_HALF_WIDTH + 1;
		if (axis_x + max_hw < 0 || axis_x - max_hw >= width)
			continue;
		const int max_row = RaptureBeamMaxRow(beam_rows);
		for (int row = 0; row <= max_row; row++)
		{
			const int y = hull_view[1] - 1 - row;
			if (y < 0 || y >= height)
				continue;
			float z = (float)ground_view[2] +
				(float)(hull_view[2] - ground_view[2]) *
				(float)(y - ground_view[1]) / (float)beam_rows;
			if (z < (float)ground_view[2])
				z = (float)ground_view[2];
			const float hw = RaptureBeamHalfWidth(row, beam_rows);
			const int scan = RaptureBeamRowScan(row, beam_rows);
			for (int dx = -scan; dx <= scan; dx++)
			{
				const int x = axis_x + dx;
				if (x < 0 || x >= width)
					continue;
				const RaptureBeamCell cell = RaptureBeamCellAt(
					dx, row, beam_rows, phase.beam_len01,
					rr->ticks_since_death, rr->seed, hull_shadow);
				if (cell.kind == RAPTURE_BEAM_NONE)
					continue;
				float cz = z + (float)HEIGHT_SCALE;
				if (cell.kind != RAPTURE_BEAM_GLOW && cell.kind != RAPTURE_BEAM_RING)
				{
					const float fx = hw * hw - (float)(dx * dx);
					cz += 6.0f * (fx > 0.0f ? sqrtf(fx) : 0.0f);
				}
				Sample* test = smp + (2 * y + 2) * (2 * width + 4) + (2 * x + 2);
				if (!test->DepthTest_RO(cz))
					continue;
				AnsiCell* dst = ptr + x + width * y;
				bool detail = false;
				if (have_snapshot)
				{
					const AnsiCell& pre =
						g_rapture_pre_actor[(size_t)x + (size_t)width * (size_t)y];
					detail = pre.gl != dst->gl || pre.fg != dst->fg || pre.bk != dst->bk;
				}
				RaptureBeamShade(&dst->gl, &dst->fg, &dst->bk,
					AverageGlyph(dst, 0xF), cell, detail);
			}
		}
	}
}

static void RenderRaptureUfos(Renderer* r, AnsiCell* ptr, int width, int height)
{
	if (!r || !r->frame_input || !ptr || !RAPTURE_UFO_VISIBLE)
		return;
	const RenderFrameInput* input = r->frame_input;
	Sprite* ufo = input->ufo_sprite;
	if (!ufo || ufo->anims <= 0 || ufo->angles <= 0 || ufo->anim[0].length <= 0)
		return;
	for (int i = 0; i < input->rapture_count && i < RENDER_MAX_RAPTURES; i++)
	{
		const RenderRapture* rr = &input->raptures[i];
		const RapturePhase phase = RaptureEval(rr->ticks_since_death);
		if (!rr->active || !phase.ufo_visible)
			continue;
		const float ufo_world[3] = {
			rr->ground[0], rr->ground[1], RaptureUfoZ(rr->ground[2], phase)};
		int view[3] = {};
		if (!ProjectCoords(r, ufo_world, view))
			continue;
		const int frame = RaptureUfoFrame(
			rr->ticks_since_death, ufo->anim[0].length);
		r->RenderSprite(ptr, width, height, ufo, false, 0, frame, 0, view);
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
	if (ObserveRenderEnabled())
	{
		// FL-4260 RQ-153d proof mode: keep water/noise phase stable so
		// before/after role-lane diffs measure only the edited Material Look lane.
		r->pn_time = 0.0;
	}
	else if (r->environment_animation_enabled)
	{
		r->pn_time += 0.02 * dt / 16666.0; // dt is in microsecs
	}
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
			r->sample_buffer.ptr[cl].material_id = -1;
			// FL-4512 U1 delete-first: the empty-frame RGB template is not a
			// mesh and owns no MaterialKey. Keep its RGB presentation, but fail
			// closed as UNKNOWN until terrain, mesh, or sprite rasterization wins.
				r->sample_buffer.ptr[cl].glyph_dispatch_surface = GLYPH_DISPATCH_UNKNOWN_VISUAL;
				r->sample_buffer.ptr[cl].s3_parity = 0;
			// FL-4208 N0a: template clears surface_id_index so the per-frame
			// clear-cache memcpy resets it (no stale terrain index carries over).
			r->sample_buffer.ptr[cl].surface_id_index = 0;
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
			r->sample_buffer.ptr[cl].material_id = -1;
			// FL-4512 U1 delete-first: resize rebuilds the same unowned
			// empty-frame template; it must never impersonate a mesh surface.
				r->sample_buffer.ptr[cl].glyph_dispatch_surface = GLYPH_DISPATCH_UNKNOWN_VISUAL;
				r->sample_buffer.ptr[cl].s3_parity = 0;
			// FL-4208 N0a: template clears surface_id_index so the per-frame
			// clear-cache memcpy resets it (no stale terrain index carries over).
			r->sample_buffer.ptr[cl].surface_id_index = 0;
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
	// (guaranteed depth fail), spare=0x8, diffuse=0xFF, a sky-blue visual, and
	// UNKNOWN dispatch ownership until a real raster surface wins.
	// Copy a prebuilt "empty frame template" into the active SampleBuffer.
	memcpy(r->sample_buffer.ptr, r->sample_buffer.ptr + dw * dh, dw*dh * sizeof(Sample));
	// FL-4512 VS2: visibility-sheet failures are frame-local observations.
	// Clearing the current invocation state here also guarantees that a frame
	// containing no meshes cannot expose a sheet left by the preceding frame.
	r->s3_mesh_parity = 0;
	r->s3_mesh_parity_valid = 0;
	r->mesh_visibility_sheet = RENDER_VISIBILITY_SHEET_INVALID;
	r->mesh_visibility_sheet_failure_count = 0;
	r->mesh_visibility_sheet_last_failure =
		RENDER_VISIBILITY_SHEET_FAILURE_NONE;

	// FL-4208 N0a (presentation-neutral): size/clear the provisional terrain
	// identity plane to the Sample buffer, reset the per-frame interner, and
	// cache this frame's world generation. BlendVR writes provisional identities
	// during rasterization; the post-raster pass below interns the survivors.
	// Nothing here changes presentation.
	{
		// proof/test-only knobs (ANCHOR_BINS sweep + trace), read once
		static bool fl4208_cfg_init = false;
		const bool isolated_observation = r->frame_input &&
			r->frame_input->debug_hooks.isolated_observation;
		if (!fl4208_cfg_init && !isolated_observation)
		{
			Fl4208ConfigInitFromEnv();
			fl4208_cfg_init = true;
		}

		const int prov_needed = dw * dh;
		if (r->fl4208_prov_cap != prov_needed)
		{
			free(r->fl4208_prov);
			r->fl4208_prov = (Fl4208ProvisionalId*)malloc(sizeof(Fl4208ProvisionalId) * (size_t)prov_needed);
			r->fl4208_prov_cap = r->fl4208_prov ? prov_needed : 0;
		}
		if (r->fl4208_prov)
			memset(r->fl4208_prov, 0, sizeof(Fl4208ProvisionalId) * (size_t)r->fl4208_prov_cap); // valid=0
		r->fl4208_interner.BeginFrame();
		r->fl4208_world_gen = GetWorldGeneration(w);
		memset(&r->fl4208_trace, 0, sizeof(r->fl4208_trace));
	}
// Product frame begin through the neutral slot: state is created once
// and carried on the Renderer as an opaque pointer. The begin slot is
// attempted whenever a product table is installed (lazy install guarantees
// it). Classic carries no product state: its create returns null and its
// begin slot is a no-op.
{
	const RendererProductV1* beg_nrt = AsciickerRenderer();
	AsciickerProductStateV1* beg_nst =
		(AsciickerProductStateV1*)r->product_runtime_state;
	if (beg_nst == nullptr && beg_nrt != nullptr &&
		beg_nrt->create != nullptr)
	{
		beg_nst = beg_nrt->create();
		r->product_runtime_state = beg_nst;
	}
	if (beg_nrt != nullptr && beg_nrt->begin_frame != nullptr)
	{
		const AsciickerProductFrameV1 beg_frame = {
			r->fl4208_frame, r->fl4208_world_gen, dw, dh };
		beg_nrt->begin_frame(beg_nst, r, &beg_frame);
	}
}

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

	// FL-4257 — derive the 4D spherical view from the server-published
	// curvature cache (RSP_JOIN intake). Spherical disables the Euclidean
	// frustum planes (the closed world has no half-space frustum; the
	// projection owner gates per vertex per image).
	int s3_frame = RenderS3Setup(r, pos, yaw, dw, dh, scene_shift);


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
	QueryWorldCB cb = { Renderer::RenderMesh , Renderer::RenderSprite };
	// FL-4876: the renderer is the only traversal that draws presentation-only
	// (INST_NO_COLLIDE) instances such as Classic trains.
	cb.include_no_collide = true;
	if (s3_frame)
	{
		// MANDATORY dual image (FL-4257): in elliptic space light reaches
		// the eye both ways around every great circle, so the whole world
		// is submitted twice — image 0 (forward hemisphere, depth = arc)
		// and image 1 (the long way around, depth = 2*pi - arc) — under
		// one arc-length depth policy. The world-model water globe
		// (world_s3.cpp) closes the geometry in BOTH images; any sample
		// still empty afterwards is measurable sky, never repainted.
		const RenderDebugHooks* s3_hooks =
			r->frame_input ? &r->frame_input->debug_hooks : 0;
		const int s3_gpu_terrain = RenderS3SubmitTerrainGpu(r, t);
		const int s3_cpu_terrain_images = s3_gpu_terrain ? 0 : 2;
		for (int image = 0; image < 2; image++)
		{
			RenderS3SetImage(r, image);
			if (image < s3_cpu_terrain_images)
				QueryTerrain(t, 0, clip_world, 0xFF, Renderer::RenderPatch, r);
			if (s3_hooks && s3_hooks->s3_after_stage)
				s3_hooks->s3_after_stage(r, image, 0, s3_hooks->user);
			QueryWorld(w, 0, clip_world, &cb, r);
			if (s3_hooks && s3_hooks->s3_after_stage)
				s3_hooks->s3_after_stage(r, image, 1, s3_hooks->user);
			WorldS3SubmitWaterGlobe(r, water);
			if (s3_hooks && s3_hooks->s3_after_stage)
				s3_hooks->s3_after_stage(r, image, 2, s3_hooks->user);
		}
		RenderS3SetImage(r, 0);
		// FL-4257 item 10 — render-side observability for the recorder
		// lane: sky census (sentinel samples after BOTH images + the
		// world-model water globe; nothing repaints them), image counts,
		// water terminations, camera pose, projection identity.
		if (out_report)
		{
			out_report->s3.s3_active = 1;
			out_report->s3.projection_identity = FL4257_PROJECTION_IDENTITY;
				out_report->s3.primary_image_count = r->s3_primary_count;
				out_report->s3.antipodal_image_count = r->s3_antipodal_count;
				out_report->s3.water_termination_count = r->s3_water_count;
				out_report->s3.gpu_terrain_used = (uint8_t)(s3_gpu_terrain ? 1 : 0);
				out_report->s3.reject_pole_count = r->s3_rej_pole;
			out_report->s3.reject_near_count = r->s3_rej_near;
			out_report->s3.reject_gate_count[0] = r->s3_rej_gate[0];
			out_report->s3.reject_gate_count[1] = r->s3_rej_gate[1];
			unsigned sky = 0;
			const int total = r->sample_buffer.w * r->sample_buffer.h;
			for (int i = 0; i < total; i++)
				if (r->sample_buffer.ptr[i].height <= -900000.0f)
					sky++;
			out_report->s3.sky_sample_count = sky;
			for (int i = 0; i < 4; i++)
				out_report->s3.camera_pose4[i] = r->s3_cam[i];
		}
		if (r->frame_input && r->frame_input->debug_hooks.after_terrain_stage)
			r->frame_input->debug_hooks.after_terrain_stage(r, r->frame_input->debug_hooks.user);
	}
	else
	{
	QueryTerrain(t, planes, clip_world, view_flags, Renderer::RenderPatch, r);
	if (r->frame_input && r->frame_input->debug_hooks.after_terrain_stage)
		r->frame_input->debug_hooks.after_terrain_stage(r, r->frame_input->debug_hooks.user);
	// World meshes compete against terrain depth immediately; sprites are only
	// queued here and composited later after the terrain image is resolved.
	QueryWorld(w, planes, clip_world, &cb, r);
	}

	// [FLOW:RENDER] Stage 4: Shadow — player blob shadow on terrain
	// WHY: The player shadow is projected onto the SampleBuffer by inverse-
	// transforming each nearby sample back to world space, computing distance
	// to the player position, and attenuating diffuse within a radius of ~2
	// world units. This runs AFTER terrain+world so the shadow falls on top.
	Invert(tm, r->inv_tm);
	Material* matlib = (Material*)GetMaterialArr();
	// FL-4257 — the blob shadow's Euclidean inverse transform has no
	// meaning on S3 (curved shadows are open work); skip under spherical.
	if (!RenderS3Active(r))
		RenderStageShadow(r, dw, dh, width, pos, scene_shift, matlib);

	////////////////////
	// [FLOW:RENDER] Stage 5: Reflection — mirror geometry below water plane
	// WHY: Water reflections are achieved by flipping the Z axis in the view
	// matrix (Z' = 2*water - Z) and re-querying terrain+world with
	// renderer reflection mode active. The reflected geometry writes into the SAME
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
	// FL-4257 — no mirrored pass on S3: a planar Z-mirror has no meaning
	// in the closed world (curved reflection is open work).
	if (!RenderS3Active(r))
	{
	r->reflection_mode = true;
	QueryTerrain(t, planes, clip_world, view_flags, Renderer::RenderPatch, r);
	QueryWorld(w, planes, clip_world, &cb, r);
	}

	r->reflection_mode = false;
	// FL-4512/FL-4652 U2: all direct, S3, and reflected mesh submissions are
	// now present.
	if (r->frame_input && r->frame_input->debug_hooks.after_reflection_stage)
		r->frame_input->debug_hooks.after_reflection_stage(r, r->frame_input->debug_hooks.user);
// Product projected-feature finalization through the neutral slot,
// attempted whenever a product table is installed: post-world, pre-resolve.
// Classic carries no projected-feature store and its slot is a no-op.
{
	const RendererProductV1* fin_nrt = AsciickerRenderer();
	AsciickerProductStateV1* fin_nst =
		(AsciickerProductStateV1*)r->product_runtime_state;
	if (fin_nrt && fin_nrt->finalize_projected_features != nullptr)
		fin_nrt->finalize_projected_features(fin_nst, r);
}

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

	// FL-4208 N0a: post-raster, PRE-resolve identity interning (dormant). Runs
	// after all terrain/world rasterization and BEFORE the resolver/presentation,
	// which remain unchanged.
	Fl4208ResolveCellIdentities(r, width, height);

// Product pre-glyph scene build through the neutral slot, attempted whenever
// a product table is installed: post-identity, pre-resolve. This is the
// single live frame call path. Classic carries no pre-glyph build and its
// slot is a no-op.
{
	const RendererProductV1* pg_nrt = AsciickerRenderer();
	AsciickerProductStateV1* pg_nst =
		(AsciickerProductStateV1*)r->product_runtime_state;
	if (pg_nrt && pg_nrt->build_pre_glyph != nullptr)
		pg_nrt->build_pre_glyph(pg_nst, r, width, height);
}

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
	RaptureSnapshotPreActor(r, out_ptr, width, height);

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
		r->s3_sprite_scale = buf->s3_scale > 1 ? buf->s3_scale : 1;
		r->s3_sprite_parity = buf->s3_parity ? 1 : 0;
		SpriteBlitDiagnostics diag = r->RenderSprite(out_ptr, width, height,
			buf->sprite, buf->refl, anim, frame, buf->angle, buf->s_pos,
			buf->rapture_dissolve);
		r->s3_sprite_scale = 1;
		r->s3_sprite_parity = 0;
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
		// FL-4131 / FL-4512: the old pre-selection sidecar writer is deleted.
		// Retain the report layout above, not its now-unreachable product log
		// string in Classic and server binaries. Product diagnostics are owned
		// by the linked product, not this shared scene orchestrator.
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

	RenderRaptureBeams(r, out_ptr, width, height);
	RenderRaptureUfos(r, out_ptr, width, height);

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
