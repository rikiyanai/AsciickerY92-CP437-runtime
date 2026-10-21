// rapture_beam.h — UFO Rapture Death beam cone: pure cell coverage + shading
//
// PURPOSE:
// The screen-space shape of the rapture beam (spec §5) as pure functions of
// (cell offset, beam height in rows, beam_len01, tick, seed), so every client
// draws the same cone for the same authoritative tick and the shape is unit
// tested (scripts/test_rapture_beam.cpp). RenderRaptureBeams
// (engine/render/render_scene.cpp) owns projection and depth testing only.
//
// LOOK (operator feedback 2026-10-08: "much wider, all yellow, with shadows"):
// - Wide cone: ~3.5 cells half-width at the hull, ~11 at the ground.
// - Warm yellow light with a shaded falloff across the cone: band 0 is the
//   bright core (219 █), band 1 mid yellow (178 ▓), band 2 amber (177 ▒),
//   band 3 dark ochre at the rim (176 ░).
// - A lit ground footprint (ellipse at the foot) ringed by a darkened shadow
//   ring, and the hull's own shadow over the top rows of the beam.
// - Edge shimmer: hash(cell, tick pair, death seed), identical on every client.
//
// RULES:
// - Pure: no globals, no engine includes; integer hash and integer colour
//   mixing only (no rand(), no float rounding in the palette maths).
// - Light pass, not paint: every colour is the frame-buffer colour already
//   there BLENDED toward a yellow target, so the terrain hue still shows.
//   "Detail" cells (sprites: the body, props; decided by the caller) keep
//   their glyph and are only tinted, so the body stays readable. Terrain
//   cells take the band's density glyph.
// - Palette is the 6x6x6 cube (16 + 36r + 6g + b). Indices outside it are
//   treated as a dark grey before blending.
//
// Coordinates: `row` 0 is the first screen row under the hull, rows grow
// toward the ground; `height_rows` = hull row - ground row, so the ground row
// is height_rows - 1. Rows up to RaptureBeamMaxRow() (nearer the viewer than
// the foot) only carry the footprint and shadow ring. `dx` is the column
// offset from the axis.

#pragma once

#include <stdint.h>

#define RAPTURE_BEAM_HULL_HALF_WIDTH    3.5f   // cells, at the hull
#define RAPTURE_BEAM_GROUND_HALF_WIDTH  11.0f  // cells, at the ground
#define RAPTURE_BEAM_MOTES              6      // spec: 4-6 motes
// Lit footprint ellipse at the foot (cells / rows), proportional to the cone.
#define RAPTURE_BEAM_GLOW_HALF_WIDTH    (RAPTURE_BEAM_GROUND_HALF_WIDTH + 2.5f)
#define RAPTURE_BEAM_GLOW_HALF_HEIGHT   3.0f
// Darkened shadow ring just outside the footprint.
#define RAPTURE_BEAM_RING_HALF_WIDTH    (RAPTURE_BEAM_GLOW_HALF_WIDTH + 2.5f)
#define RAPTURE_BEAM_RING_HALF_HEIGHT   (RAPTURE_BEAM_GLOW_HALF_HEIGHT + 1.0f)
// Rows below the ground row that can hold footprint/ring cells.
#define RAPTURE_BEAM_FOOT_ROWS          4
// Rows under the hull darkened by the hull's own shadow (when the UFO shows).
#define RAPTURE_BEAM_HULL_SHADOW_ROWS   2
#define RAPTURE_BEAM_MOTE_TICKS_PER_ROW 2u     // mote rise speed: one row per 2 ticks

enum RaptureBeamCellKind : uint8_t
{
	RAPTURE_BEAM_NONE = 0,
	RAPTURE_BEAM_CORE,   // cone bands 0-1
	RAPTURE_BEAM_EDGE,   // cone bands 2-3
	RAPTURE_BEAM_MOTE,
	RAPTURE_BEAM_GLOW,   // lit ground footprint
	RAPTURE_BEAM_RING,   // shadow ring around the footprint
};

struct RaptureBeamCell
{
	uint8_t kind;    // RaptureBeamCellKind
	uint8_t glyph;   // CP437 glyph (cone density ramp / mote); 0 for GLOW/RING
	uint8_t band;    // 0 = brightest .. 3 = darkest (cone, glow); 0 for ring/mote
	uint8_t shadow;  // hull shadow strength 0..2 (top rows of the cone)
};

static inline uint32_t RaptureBeamHash(uint32_t a, uint32_t b, uint32_t c, uint32_t d)
{
	uint32_t h = 0x9E3779B9u ^ a;
	h = (h ^ (b + 0x7F4A7C15u)) * 0x85EBCA6Bu;
	h ^= h >> 13;
	h = (h ^ (c + 0x165667B1u)) * 0xC2B2AE35u;
	h ^= h >> 16;
	h = (h ^ (d + 0x27D4EB2Fu)) * 0x85EBCA6Bu;
	h ^= h >> 15;
	return h;
}

// Rows from the hull that are lit for a given beam length (0..height_rows).
static inline int RaptureBeamVisibleRows(int height_rows, float len01)
{
	if (height_rows <= 0 || !(len01 > 0.0f))
		return 0;
	if (len01 >= 1.0f)
		return height_rows;
	const float rows = len01 * (float)height_rows;
	int vis = (int)rows;
	if ((float)vis < rows)
		vis++;
	return vis > height_rows ? height_rows : vis;
}

// Last row index that can carry a beam cell (footprint / ring rows included).
static inline int RaptureBeamMaxRow(int height_rows)
{
	return height_rows - 1 + RAPTURE_BEAM_FOOT_ROWS;
}

// Cone half-width (cells) at `row`, linear from the hull to the ground row.
static inline float RaptureBeamHalfWidth(int row, int height_rows)
{
	const int span = height_rows > 1 ? height_rows - 1 : 1;
	float t = (float)row / (float)span;
	t = t < 0.0f ? 0.0f : (t > 1.0f ? 1.0f : t);
	return RAPTURE_BEAM_HULL_HALF_WIDTH +
		(RAPTURE_BEAM_GROUND_HALF_WIDTH - RAPTURE_BEAM_HULL_HALF_WIDTH) * t;
}

static inline int RaptureBeamAbs(int v) { return v < 0 ? -v : v; }

// Column extent the renderer scans on `row`: the cone, or the footprint ring
// near the foot. Keeps the per-frame cell count to the cells that can be lit.
static inline int RaptureBeamRowScan(int row, int height_rows)
{
	int ext = 0;
	if (row < height_rows)
		ext = (int)(RaptureBeamHalfWidth(row, height_rows) + 0.25f);
	const float ey = (float)(row - (height_rows - 1)) / RAPTURE_BEAM_RING_HALF_HEIGHT;
	if (ey > -1.0f && ey < 1.0f)
	{
		// ring half-width at this row: hw * sqrt(1 - ey^2) <= hw; use the bound
		const int ring = (int)RAPTURE_BEAM_RING_HALF_WIDTH + 1;
		ext = ring > ext ? ring : ext;
	}
	return ext;
}

// Mote i position for this tick: rises from the ground row to the hull, then
// wraps with a new column (cycle index is hashed in).
static inline bool RaptureBeamMote(int i, int height_rows, uint32_t tick, uint32_t seed,
	int* out_dx, int* out_row, uint8_t* out_glyph)
{
	if (height_rows < 3)
		return false;
	// One shared rise speed with evenly spaced phases, so motes never share a
	// row while the beam is at least RAPTURE_BEAM_MOTES rows tall.
	const uint32_t travel = (uint32_t)(height_rows - 1);
	const uint32_t step = tick / RAPTURE_BEAM_MOTE_TICKS_PER_ROW +
		((uint32_t)i * travel) / (uint32_t)RAPTURE_BEAM_MOTES +
		RaptureBeamHash(seed, 0xA11CEu, 0u, 0u) % travel;
	const uint32_t cycle = step / travel;
	const int row = (height_rows - 1) - (int)(step % travel); // rises: row shrinks
	const uint32_t h1 = RaptureBeamHash((uint32_t)i, seed, cycle, 0xB0B0u);
	int hw = (int)RaptureBeamHalfWidth(row, height_rows) - 1;
	if (hw < 0)
		hw = 0;
	*out_dx = (int)(h1 % (uint32_t)(2 * hw + 1)) - hw;
	*out_row = row;
	static const uint8_t glyphs[3] = { 250, 249, 15 }; // · ∙ ☼
	*out_glyph = glyphs[(h1 >> 12) % 3u];
	return true;
}

// The beam cell at (dx, row). Pure function of its arguments.
// hull_shadow: the UFO hull is drawn above the beam (RAPTURE_UFO_VISIBLE),
// so the top rows of the cone sit in its shadow.
static inline RaptureBeamCell RaptureBeamCellAt(int dx, int row, int height_rows,
	float len01, uint32_t tick, uint32_t seed, bool hull_shadow = true)
{
	RaptureBeamCell out = { RAPTURE_BEAM_NONE, 0, 0, 0 };
	if (height_rows < 2 || row < 0 || row > RaptureBeamMaxRow(height_rows))
		return out;
	const int vis = RaptureBeamVisibleRows(height_rows, len01);
	if (vis <= 0)
		return out;
	const int adx = RaptureBeamAbs(dx);

	if (row < vis && row < height_rows)
	{
		const float hw = RaptureBeamHalfWidth(row, height_rows);
		if ((float)adx <= hw + 0.25f)
		{
			const uint8_t shadow = (hull_shadow && row < RAPTURE_BEAM_HULL_SHADOW_ROWS)
				? (uint8_t)(RAPTURE_BEAM_HULL_SHADOW_ROWS - row) : (uint8_t)0;
			// motes take precedence inside the lit cone
			for (int i = 0; i < RAPTURE_BEAM_MOTES; i++)
			{
				int mdx = 0, mrow = 0;
				uint8_t mg = 0;
				if (RaptureBeamMote(i, height_rows, tick, seed, &mdx, &mrow, &mg) &&
					mdx == dx && mrow == row)
				{
					out.kind = RAPTURE_BEAM_MOTE;
					out.glyph = mg;
					out.band = 0;
					out.shadow = shadow;
					return out;
				}
			}
			static const uint8_t ramp[4] = { 219, 178, 177, 176 }; // █ ▓ ▒ ░
			const float n = (float)adx / (hw + 0.5f);
			int band = n < 0.24f ? 0 : (n < 0.50f ? 1 : (n < 0.76f ? 2 : 3));
			const bool leading = len01 < 1.0f && row == vis - 1; // growing/retracting tip
			if (leading && band < 2)
				band = 2;
			if (band >= 2)
			{
				// shimmer: per (cell, tick-pair) flicker, identical on every client
				const uint32_t h = RaptureBeamHash((uint32_t)dx, (uint32_t)row, tick / 2u, seed);
				switch (h & 7u)
				{
				case 0:
					if (band == 3)
						return out; // rim cell flickers out
					band = 3;
					break;
				case 1: band = band == 2 ? 3 : 2; break;
				default: break;
				}
			}
			out.kind = band < 2 ? RAPTURE_BEAM_CORE : RAPTURE_BEAM_EDGE;
			out.band = (uint8_t)band;
			out.glyph = ramp[band];
			out.shadow = shadow;
			return out;
		}
	}

	// Ground footprint and its shadow ring, once the beam touches down.
	if (vis >= height_rows)
	{
		const float gy = (float)(row - (height_rows - 1));
		const float ex = (float)dx / RAPTURE_BEAM_GLOW_HALF_WIDTH;
		const float ey = gy / RAPTURE_BEAM_GLOW_HALF_HEIGHT;
		const float d2 = ex * ex + ey * ey;
		if (d2 < 0.97f) // strict: no single-cell tips at the ellipse ends
		{
			out.kind = RAPTURE_BEAM_GLOW;
			out.band = d2 < 0.30f ? 1 : (d2 < 0.65f ? 2 : 3);
			return out;
		}
		const float rx = (float)dx / RAPTURE_BEAM_RING_HALF_WIDTH;
		const float ry = gy / RAPTURE_BEAM_RING_HALF_HEIGHT;
		if (rx * rx + ry * ry < 0.97f)
		{
			out.kind = RAPTURE_BEAM_RING;
			return out;
		}
	}
	return out;
}

// ─── Shading (integer palette maths; caller supplies the cell's average colour) ──

struct RaptureBeamRgb { int r, g, b; };

static inline RaptureBeamRgb RaptureBeamDecode(int c)
{
	RaptureBeamRgb o;
	if (c < 16 || c > 231)
	{
		o.r = o.g = o.b = 1;
		return o;
	}
	int v = c - 16;
	o.r = v / 36; v -= 36 * o.r;
	o.g = v / 6;  v -= 6 * o.g;
	o.b = v;
	return o;
}

static inline int RaptureBeamEncode(RaptureBeamRgb c)
{
	c.r = c.r < 0 ? 0 : (c.r > 5 ? 5 : c.r);
	c.g = c.g < 0 ? 0 : (c.g > 5 ? 5 : c.g);
	c.b = c.b < 0 ? 0 : (c.b > 5 ? 5 : c.b);
	return 16 + 36 * c.r + 6 * c.g + c.b;
}

// Blend cube colour c toward target t by a16/16 (round half up, integers only).
static inline int RaptureBeamMix(int c, RaptureBeamRgb t, int a16)
{
	a16 = a16 < 0 ? 0 : (a16 > 16 ? 16 : a16);
	const RaptureBeamRgb s = RaptureBeamDecode(c);
	RaptureBeamRgb o;
	o.r = (s.r * 16 + (t.r - s.r) * a16 + 8) >> 4;
	o.g = (s.g * 16 + (t.g - s.g) * a16 + 8) >> 4;
	o.b = (s.b * 16 + (t.b - s.b) * a16 + 8) >> 4;
	return RaptureBeamEncode(o);
}

// Light colours by band (cube levels): bright pale yellow core -> yellow ->
// amber -> dark ochre at the rim. Fg carries the light, bk the lit ground.
// The bk targets lean red-over-green (amber/ochre) on purpose: a partial
// blend of green grass toward pure yellow lands on yellow-green, so the hue
// is shifted toward amber to make the mix read yellow on any ground.
static inline RaptureBeamRgb RaptureBeamBandFg(int band)
{
	static const RaptureBeamRgb t[4] = { {5, 5, 2}, {5, 5, 1}, {5, 4, 0}, {4, 2, 0} };
	return t[band < 0 ? 0 : (band > 3 ? 3 : band)];
}
static inline RaptureBeamRgb RaptureBeamBandBk(int band)
{
	static const RaptureBeamRgb t[4] = { {5, 5, 1}, {5, 4, 0}, {4, 3, 0}, {3, 2, 0} };
	return t[band < 0 ? 0 : (band > 3 ? 3 : band)];
}
// Blend strengths (of 16) per band: fg (light) and bk (ground under it).
static inline int RaptureBeamBandFgA(int band)
{
	static const int a[4] = { 15, 15, 14, 13 };
	return a[band < 0 ? 0 : (band > 3 ? 3 : band)];
}
static inline int RaptureBeamBandBkA(int band)
{
	static const int a[4] = { 13, 12, 12, 11 };
	return a[band < 0 ? 0 : (band > 3 ? 3 : band)];
}

static const RaptureBeamRgb kRaptureBeamShadowRgb = { 0, 0, 0 };
static const RaptureBeamRgb kRaptureBeamHullShadowRgb = { 2, 1, 0 };
static const RaptureBeamRgb kRaptureBeamMoteRgb[2] = { {5, 5, 5}, {5, 5, 3} };

// Fallback "detail" test for callers without a pre-actor snapshot: true when
// the cell carries a glyph worth keeping (not a uniform fill).
static inline bool RaptureBeamUniformCell(uint8_t gl, uint8_t fg, uint8_t bk)
{
	return gl == 0 || gl == 32 || gl == 219 || fg == bk || (gl >= 176 && gl <= 178);
}

// Shade one frame-buffer cell in place. `avg` = the cell's average colour
// (AverageGlyph(cell, 0xF) in the renderer). `detail` = the cell belongs to a
// sprite (body, prop) and keeps its glyph; otherwise (terrain) it takes the
// beam's density glyph.
static inline void RaptureBeamShade(uint8_t* gl, uint8_t* fg, uint8_t* bk, int avg,
	RaptureBeamCell cell, bool detail)
{
	switch (cell.kind)
	{
	case RAPTURE_BEAM_CORE:
	case RAPTURE_BEAM_EDGE:
	{
		const RaptureBeamRgb tf = RaptureBeamBandFg(cell.band);
		const RaptureBeamRgb tb = RaptureBeamBandBk(cell.band);
		int f, b;
		if (!detail)
		{
			*gl = cell.glyph;
			f = RaptureBeamMix(avg, tf, RaptureBeamBandFgA(cell.band));
			b = RaptureBeamMix(avg, tb, RaptureBeamBandBkA(cell.band));
		}
		else
		{
			// keep the body/prop glyph: tint both colours toward warm light
			// (amber-shifted so half-covered grass does not turn lime)
			static const RaptureBeamRgb detail_fg = { 5, 4, 1 };
			static const RaptureBeamRgb detail_bk = { 5, 3, 0 };
			f = RaptureBeamMix(*fg, detail_fg, RaptureBeamBandFgA(cell.band) / 2);
			b = RaptureBeamMix(*bk, detail_bk, RaptureBeamBandBkA(cell.band) / 2);
		}
		if (cell.shadow)
		{
			f = RaptureBeamMix(f, kRaptureBeamHullShadowRgb, 4 * cell.shadow);
			b = RaptureBeamMix(b, kRaptureBeamHullShadowRgb, 5 * cell.shadow);
		}
		*fg = (uint8_t)f;
		*bk = (uint8_t)b;
		break;
	}
	case RAPTURE_BEAM_MOTE:
	{
		*gl = cell.glyph;
		*fg = (uint8_t)RaptureBeamEncode(kRaptureBeamMoteRgb[cell.glyph == 15 ? 0 : 1]);
		*bk = (uint8_t)RaptureBeamMix(avg, RaptureBeamBandBk(1), RaptureBeamBandBkA(0));
		break;
	}
	case RAPTURE_BEAM_GLOW:
	{
		// lit ground: keep the terrain glyph (it shows through), blend toward
		// yellow (fg) and amber (bk); band 1 centre .. band 3 rim
		static const RaptureBeamRgb glow_fg[2] = { {5, 5, 1}, {5, 4, 1} };
		static const RaptureBeamRgb glow_bk[2] = { {5, 4, 0}, {5, 3, 0} };
		const int s = 3 - (cell.band > 3 ? 3 : (cell.band < 1 ? 1 : cell.band)); // 2..0
		*fg = (uint8_t)RaptureBeamMix(*fg, glow_fg[s == 2 ? 0 : 1], 10 + 2 * s);
		*bk = (uint8_t)RaptureBeamMix(*bk, glow_bk[s == 2 ? 0 : 1], 8 + 2 * s);
		break;
	}
	case RAPTURE_BEAM_RING:
		*fg = (uint8_t)RaptureBeamMix(*fg, kRaptureBeamShadowRgb, 7);
		*bk = (uint8_t)RaptureBeamMix(*bk, kRaptureBeamShadowRgb, 7);
		break;
	default:
		break;
	}
}

// Back-compat overload (synthetic preview tool): detail from the glyph.
static inline void RaptureBeamShade(uint8_t* gl, uint8_t* fg, uint8_t* bk, int avg, RaptureBeamCell cell)
{
	RaptureBeamShade(gl, fg, bk, avg, cell, !RaptureBeamUniformCell(*gl, *fg, *bk));
}
