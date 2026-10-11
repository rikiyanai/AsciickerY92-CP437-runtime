// test_rapture_beam.cpp — unit test for engine/rapture_beam.h
//
// Built and run by scripts/test_rapture_timeline.py. The beam cell coverage
// is a pure function of (dx, row, height_rows, len01, tick, seed); this test
// pins its shape: nothing at len01=0, growth monotonic in len01, ~3-4 cells
// half-width at the hull and ~10-12 at the ground, a bright core shading to
// darker amber edges (█▓▒░ ramp), deterministic flicker that does change with
// the tick, 4-6 motes that drift upward, a wide lit footprint with a shadow
// ring outside it once the beam touches down, the hull shadow on the beam
// top, a yellow (not green/cyan) read on green, sand and dark ground, and a
// bounded per-beam cell count.
//
// With `--shade-grid <in> <out> <height_rows> <len01> <tick> <seed> <axis_x>
// <hull_y>` it shades a text cell grid (one "gl fg bk" triple per cell,
// row-major, bottom row first) the same way RenderRaptureBeams does minus
// depth testing. scripts/adhoc/2026-10-08-render-rapture-beam-frame.py uses
// that to make the synthetic docs/previews/ufo/beam-frame.png.

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "rapture_timeline.h"
#include "rapture_beam.h"

static int g_failures = 0;

#define CHECK(cond) do { if (!(cond)) { \
	fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
	g_failures++; } } while (0)

static const int kW = 20; // scan |dx| past the shadow ring edge

static int CountCells(int H, float len01, uint32_t tick, uint32_t seed, int kind = -1)
{
	int n = 0;
	for (int row = 0; row <= RaptureBeamMaxRow(H) + 2; row++)
		for (int dx = -kW; dx <= kW; dx++)
		{
			const RaptureBeamCell c = RaptureBeamCellAt(dx, row, H, len01, tick, seed);
			if (c.kind != RAPTURE_BEAM_NONE && (kind < 0 || c.kind == kind))
				n++;
		}
	return n;
}

static int RowExtent(int H, int row, float len01, uint32_t tick, uint32_t seed)
{
	int ext = -1;
	for (int dx = -kW; dx <= kW; dx++)
	{
		const RaptureBeamCell c = RaptureBeamCellAt(dx, row, H, len01, tick, seed);
		if (c.kind == RAPTURE_BEAM_CORE || c.kind == RAPTURE_BEAM_EDGE || c.kind == RAPTURE_BEAM_MOTE)
			ext = abs(dx) > ext ? abs(dx) : ext;
	}
	return ext;
}

static RaptureBeamRgb Rgb(int c) { return RaptureBeamDecode(c); }
static int Cube(int r, int g, int b) { return 16 + 36 * r + 6 * g + b; }

static void TestClosedAndGrowth()
{
	const int H = 10;
	CHECK(CountCells(H, 0.0f, 60, 7) == 0);
	CHECK(CountCells(H, -1.0f, 60, 7) == 0);
	CHECK(RaptureBeamVisibleRows(H, 0.0f) == 0);
	CHECK(RaptureBeamVisibleRows(H, 0.05f) == 1);
	CHECK(RaptureBeamVisibleRows(H, 1.0f) == H);
	CHECK(RaptureBeamVisibleRows(H, 2.0f) == H);
	// Rows below the visible length are never lit (growth from the hull).
	for (int step = 1; step <= 20; step++)
	{
		const float len = step / 20.0f;
		const int vis = RaptureBeamVisibleRows(H, len);
		for (int row = vis; row <= RaptureBeamMaxRow(H); row++)
			for (int dx = -kW; dx <= kW; dx++)
			{
				const RaptureBeamCell c = RaptureBeamCellAt(dx, row, H, len, 60, 7);
				CHECK(c.kind == RAPTURE_BEAM_NONE || c.kind == RAPTURE_BEAM_GLOW || c.kind == RAPTURE_BEAM_RING);
				if (vis < H) // footprint/ring only once the tip reaches the ground
					CHECK(c.kind == RAPTURE_BEAM_NONE);
			}
	}
	// Visible rows grow monotonically with len01.
	int prev = 0;
	for (int step = 0; step <= 40; step++)
	{
		const int vis = RaptureBeamVisibleRows(H, step / 40.0f);
		CHECK(vis >= prev);
		prev = vis;
	}
	// The BEAM_OPEN/BEAM_CLOSE phases drive the same function.
	CHECK(CountCells(H, RaptureEval(RAPTURE_BEAM_OPEN_START_TICKS).beam_len01, 54, 7) == 0);
	CHECK(CountCells(H, RaptureEval(90).beam_len01, 90, 7) > CountCells(H, RaptureEval(57).beam_len01, 57, 7));
	CHECK(CountCells(H, RaptureEval(RAPTURE_DEPART_START_TICKS).beam_len01, 123, 7) == 0);
	// Nothing past the last footprint row.
	for (int dx = -kW; dx <= kW; dx++)
		CHECK(RaptureBeamCellAt(dx, RaptureBeamMaxRow(H) + 1, H, 1.0f, 90, 7).kind == RAPTURE_BEAM_NONE);
}

static void TestConeShape()
{
	for (int H = 6; H <= 16; H += 2)
	{
		CHECK(RaptureBeamHalfWidth(0, H) >= 3.0f && RaptureBeamHalfWidth(0, H) <= 4.0f);
		CHECK(RaptureBeamHalfWidth(H - 1, H) >= 10.0f && RaptureBeamHalfWidth(H - 1, H) <= 12.0f);
		float prev = 0.0f;
		for (int row = 0; row < H; row++)
		{
			const float hw = RaptureBeamHalfWidth(row, H);
			CHECK(hw + 1e-5f >= prev);
			prev = hw;
		}
		// Across ticks, the lit extent never exceeds the cone, and the max
		// extent is ~3-4 at the hull and ~10-12 at the ground (3x the old cone).
		int hull_max = 0, ground_max = 0;
		for (uint32_t tick = 66; tick < 114; tick++)
		{
			for (int row = 0; row < H; row++)
			{
				CHECK(RowExtent(H, row, 1.0f, tick, 11) <= (int)(RaptureBeamHalfWidth(row, H) + 0.25f));
				CHECK(RowExtent(H, row, 1.0f, tick, 11) <= RaptureBeamRowScan(row, H));
			}
			const int e0 = RowExtent(H, 0, 1.0f, tick, 11);
			const int eg = RowExtent(H, H - 1, 1.0f, tick, 11);
			hull_max = e0 > hull_max ? e0 : hull_max;
			ground_max = eg > ground_max ? eg : ground_max;
		}
		CHECK(hull_max >= 3 && hull_max <= 4);
		CHECK(ground_max >= 10 && ground_max <= 12);
		// The axis is a solid core cell on every lit row, apart from motes.
		for (int row = 0; row < H; row++)
		{
			const RaptureBeamCell c = RaptureBeamCellAt(0, row, H, 1.0f, 80, 11);
			CHECK(c.kind == RAPTURE_BEAM_CORE || c.kind == RAPTURE_BEAM_MOTE);
			if (c.kind == RAPTURE_BEAM_CORE)
				CHECK(c.glyph == 219 && c.band == 0);
		}
		// Shaded falloff: band (and ramp glyph) never gets brighter outward.
		static const uint8_t ramp[4] = { 219, 178, 177, 176 };
		for (int row = 0; row < H; row++)
		{
			int prev_band = 0;
			bool seen[4] = { false, false, false, false };
			for (int dx = 0; dx <= kW; dx++)
			{
				const RaptureBeamCell c = RaptureBeamCellAt(dx, row, H, 1.0f, 80, 11);
				if (c.kind == RAPTURE_BEAM_CORE || c.kind == RAPTURE_BEAM_EDGE)
				{
					CHECK(c.band <= 3 && c.glyph == ramp[c.band]);
					CHECK(c.kind == (c.band < 2 ? RAPTURE_BEAM_CORE : RAPTURE_BEAM_EDGE));
					if (c.band < 2)
						CHECK(c.band >= prev_band); // core bands never flicker
					prev_band = c.band > prev_band ? c.band : prev_band;
					seen[c.band] = true;
				}
			}
			if (row == H - 1)
				CHECK(seen[0] && seen[1] && (seen[2] || seen[3])); // all of the ramp at the foot
		}
	}
}

static void TestDeterminismAndFlicker()
{
	const int H = 11;
	bool any_change = false;
	for (uint32_t tick = 66; tick < 114; tick++)
		for (int row = 0; row <= RaptureBeamMaxRow(H); row++)
			for (int dx = -kW; dx <= kW; dx++)
			{
				const RaptureBeamCell a = RaptureBeamCellAt(dx, row, H, 1.0f, tick, 4242);
				const RaptureBeamCell b = RaptureBeamCellAt(dx, row, H, 1.0f, tick, 4242);
				CHECK(a.kind == b.kind && a.glyph == b.glyph && a.band == b.band && a.shadow == b.shadow);
				const RaptureBeamCell c = RaptureBeamCellAt(dx, row, H, 1.0f, tick + 2, 4242);
				if (c.kind != a.kind || c.glyph != a.glyph)
					any_change = true;
			}
	CHECK(any_change); // shimmer and motes animate
	// Core cells never flicker out.
	for (uint32_t tick = 0; tick < 64; tick++)
		CHECK(RaptureBeamCellAt(0, 2, H, 1.0f, tick, 3).kind != RAPTURE_BEAM_NONE);
}

static void TestMotes()
{
	for (int H = 7; H <= 16; H += 3)
		for (uint32_t tick = 66; tick < 114; tick++)
		{
			const int motes = CountCells(H, 1.0f, tick, 99, RAPTURE_BEAM_MOTE);
			CHECK(motes >= 4 && motes <= 6); // distinct rows once H-1 >= motes
			CHECK(RAPTURE_BEAM_MOTES >= 4 && RAPTURE_BEAM_MOTES <= 6);
		}
	// Each mote moves up (row shrinks) or wraps back to the ground.
	const int H = 12;
	for (int i = 0; i < RAPTURE_BEAM_MOTES; i++)
	{
		int prev_row = -1;
		int ups = 0;
		for (uint32_t tick = 0; tick < 120; tick++)
		{
			int dx = 0, row = 0;
			uint8_t g = 0;
			CHECK(RaptureBeamMote(i, H, tick, 99, &dx, &row, &g));
			CHECK(row >= 1 && row <= H - 1);
			CHECK(g == 250 || g == 249 || g == 15);
			CHECK((float)abs(dx) <= RaptureBeamHalfWidth(row, H));
			if (prev_row >= 0)
			{
				CHECK(row == prev_row || row == prev_row - 1 || row == H - 1);
				if (row == prev_row - 1)
					ups++;
			}
			prev_row = row;
		}
		CHECK(ups > 10);
	}
}

static void TestFootprintAndShadowRing()
{
	const int H = 10;
	CHECK(CountCells(H, 0.85f, 70, 5, RAPTURE_BEAM_GLOW) == 0); // tip one row short of the ground
	CHECK(CountCells(H, 0.85f, 70, 5, RAPTURE_BEAM_RING) == 0);
	CHECK(RaptureBeamVisibleRows(H, 0.85f) == H - 1);
	CHECK(CountCells(H, 1.0f, 70, 5, RAPTURE_BEAM_GLOW) > 0);
	CHECK(CountCells(H, 1.0f, 70, 5, RAPTURE_BEAM_RING) > 0);
	// The footprint sits at the foot, wider than the cone; the ring lies
	// outside the footprint ellipse (never inside it), farther out still.
	int glow_ext = 0, ring_ext = 0;
	for (int row = 0; row <= RaptureBeamMaxRow(H); row++)
		for (int dx = -kW; dx <= kW; dx++)
		{
			const RaptureBeamCell c = RaptureBeamCellAt(dx, row, H, 1.0f, 70, 5);
			const float gy = (float)(row - (H - 1));
			if (c.kind == RAPTURE_BEAM_GLOW || c.kind == RAPTURE_BEAM_RING)
			{
				CHECK(fabsf(gy) <= RAPTURE_BEAM_RING_HALF_HEIGHT);
				CHECK(abs(dx) <= RaptureBeamRowScan(row, H));
			}
			if (c.kind == RAPTURE_BEAM_GLOW)
			{
				CHECK(c.band >= 1 && c.band <= 3);
				glow_ext = abs(dx) > glow_ext ? abs(dx) : glow_ext;
			}
			if (c.kind == RAPTURE_BEAM_RING)
			{
				const float ex = (float)dx / RAPTURE_BEAM_GLOW_HALF_WIDTH;
				const float ey = gy / RAPTURE_BEAM_GLOW_HALF_HEIGHT;
				CHECK(ex * ex + ey * ey >= 0.97f);
				ring_ext = abs(dx) > ring_ext ? abs(dx) : ring_ext;
			}
		}
	CHECK(glow_ext >= 12 && glow_ext > (int)RAPTURE_BEAM_GROUND_HALF_WIDTH);
	CHECK(ring_ext > glow_ext);
	// Footprint rows below the ground row exist (the near half of the ellipse).
	CHECK(RaptureBeamCellAt(0, H + 1, H, 1.0f, 70, 5).kind == RAPTURE_BEAM_GLOW);
}

static void TestHullShadow()
{
	const int H = 11;
	for (int row = 0; row < H; row++)
		for (int dx = -kW; dx <= kW; dx++)
		{
			const RaptureBeamCell on = RaptureBeamCellAt(dx, row, H, 1.0f, 90, 8, true);
			const RaptureBeamCell off = RaptureBeamCellAt(dx, row, H, 1.0f, 90, 8, false);
			CHECK(on.kind == off.kind && on.glyph == off.glyph && on.band == off.band);
			CHECK(off.shadow == 0);
			if (on.kind == RAPTURE_BEAM_CORE || on.kind == RAPTURE_BEAM_EDGE)
			{
				if (row < RAPTURE_BEAM_HULL_SHADOW_ROWS)
					CHECK(on.shadow == RAPTURE_BEAM_HULL_SHADOW_ROWS - row);
				else
					CHECK(on.shadow == 0);
			}
		}
	// Shadowed cells shade darker than the same cell in full light.
	RaptureBeamCell lit = { RAPTURE_BEAM_CORE, 219, 0, 0 };
	RaptureBeamCell dim = { RAPTURE_BEAM_CORE, 219, 0, 2 };
	const int grass = Cube(1, 3, 1);
	uint8_t g1 = ',', f1 = (uint8_t)grass, b1 = (uint8_t)grass;
	uint8_t g2 = ',', f2 = (uint8_t)grass, b2 = (uint8_t)grass;
	RaptureBeamShade(&g1, &f1, &b1, grass, lit, false);
	RaptureBeamShade(&g2, &f2, &b2, grass, dim, false);
	const RaptureBeamRgb a = Rgb(f1), b = Rgb(f2);
	CHECK(b.r + b.g + b.b < a.r + a.g + a.b);
}

static void TestYellowOnAnyGround()
{
	// Grass, sand, dark ground, water-ish blue: the shaded light reads yellow
	// (red and green lead blue; red keeps up with green) in every band.
	const int grounds[4] = { Cube(1, 3, 1), Cube(4, 4, 2), Cube(1, 1, 1), Cube(0, 1, 3) };
	for (int gi = 0; gi < 4; gi++)
		for (int band = 0; band < 4; band++)
		{
			RaptureBeamCell cell = { (uint8_t)(band < 2 ? RAPTURE_BEAM_CORE : RAPTURE_BEAM_EDGE),
				(uint8_t)(band == 0 ? 219 : 176 + 3 - band), (uint8_t)band, 0 };
			uint8_t gl = ',', fg = (uint8_t)grounds[gi], bk = (uint8_t)grounds[gi];
			RaptureBeamShade(&gl, &fg, &bk, grounds[gi], cell, false);
			CHECK(gl == cell.glyph); // terrain takes the density ramp
			const RaptureBeamRgb f = Rgb(fg);
			CHECK(f.r >= f.g);
			CHECK(f.g > f.b);
			CHECK(f.r >= 3);
			if (band == 0)
				CHECK(f.r == 5 && f.g == 5 && f.b <= 2);
		}
	// The core is brighter than the rim.
	const int grass = grounds[0];
	RaptureBeamCell core = { RAPTURE_BEAM_CORE, 219, 0, 0 };
	RaptureBeamCell rim = { RAPTURE_BEAM_EDGE, 176, 3, 0 };
	uint8_t g1 = ',', f1 = (uint8_t)grass, b1 = (uint8_t)grass;
	uint8_t g2 = ',', f2 = (uint8_t)grass, b2 = (uint8_t)grass;
	RaptureBeamShade(&g1, &f1, &b1, grass, core, false);
	RaptureBeamShade(&g2, &f2, &b2, grass, rim, false);
	const RaptureBeamRgb c1 = Rgb(f1), c2 = Rgb(f2);
	CHECK(c1.r + c1.g + c1.b > c2.r + c2.g + c2.b);
	// Motes are bright yellow/white.
	static const uint8_t mote_glyphs[3] = { 250, 249, 15 };
	for (uint8_t mg : mote_glyphs)
	{
		RaptureBeamCell m = { RAPTURE_BEAM_MOTE, mg, 0, 0 };
		uint8_t gl = ',', fg = (uint8_t)grass, bk = (uint8_t)grass;
		RaptureBeamShade(&gl, &fg, &bk, grass, m, false);
		const RaptureBeamRgb f = Rgb(fg);
		CHECK(gl == mg && f.r == 5 && f.g == 5 && f.b >= 3);
	}
}

static void TestShadeDetailAndRing()
{
	// Detailed (sprite) cells keep their glyph: the body stays readable.
	uint8_t gl = 'v', fg = (uint8_t)Cube(2, 0, 0), bk = (uint8_t)Cube(0, 0, 2);
	RaptureBeamCell core = { RAPTURE_BEAM_CORE, 219, 0, 0 };
	RaptureBeamShade(&gl, &fg, &bk, Cube(1, 0, 1), core, true);
	CHECK(gl == 'v');
	CHECK(fg != Cube(5, 5, 2)); // tinted, not painted over
	// Terrain cells take the density glyph.
	gl = ','; fg = bk = (uint8_t)Cube(1, 3, 1);
	RaptureBeamShade(&gl, &fg, &bk, Cube(1, 3, 1), core, false);
	CHECK(gl == 219);
	// Lit footprint keeps the terrain glyph and warms it.
	RaptureBeamCell glow = { RAPTURE_BEAM_GLOW, 0, 1, 0 };
	gl = ','; fg = bk = (uint8_t)Cube(1, 3, 1);
	RaptureBeamShade(&gl, &fg, &bk, Cube(1, 3, 1), glow, false);
	CHECK(gl == ',' && Rgb(fg).r > 1 && Rgb(bk).r > 1);
	// Shadow ring only darkens, and does darken.
	for (int c = 16; c <= 231; c++)
	{
		RaptureBeamCell ring = { RAPTURE_BEAM_RING, 0, 0, 0 };
		uint8_t g = '.', f = (uint8_t)c, b = (uint8_t)c;
		RaptureBeamShade(&g, &f, &b, c, ring, false);
		const RaptureBeamRgb s = Rgb(c), o = Rgb(f);
		CHECK(g == '.');
		CHECK(o.r <= s.r && o.g <= s.g && o.b <= s.b);
		if (s.r >= 2 || s.g >= 2 || s.b >= 2)
			CHECK(o.r + o.g + o.b < s.r + s.g + s.b);
	}
	// Integer mix stays on the cube for every input.
	for (int c = 0; c <= 255; c++)
		for (int a = 0; a <= 16; a++)
		{
			const int m = RaptureBeamMix(c, RaptureBeamBandFg(a & 3), a);
			CHECK(m >= 16 && m <= 231);
		}
}

static void TestCellBudget()
{
	// RenderRaptureBeams scans rows 0..RaptureBeamMaxRow(H), each over
	// +/- RaptureBeamRowScan(row, H) columns. Keep that modest: <= 700 cells
	// per beam for any beam up to 16 rows (the hover beam is ~11 rows), so
	// the 16-beam cap stays around 11k cell tests per frame at worst.
	for (int H = 2; H <= 16; H++)
	{
		int scanned = 0;
		for (int row = 0; row <= RaptureBeamMaxRow(H); row++)
			scanned += 2 * RaptureBeamRowScan(row, H) + 1;
		CHECK(scanned <= 700);
		CHECK(CountCells(H, 1.0f, 90, 1) <= scanned);
	}
}

static int ShadeGrid(int argc, char** argv)
{
	if (argc < 10)
		return 2;
	FILE* in = fopen(argv[2], "r");
	FILE* out = fopen(argv[3], "w");
	if (!in || !out)
		return 2;
	const int H = atoi(argv[4]);
	const float len01 = (float)atof(argv[5]);
	const uint32_t tick = (uint32_t)strtoul(argv[6], 0, 10);
	const uint32_t seed = (uint32_t)strtoul(argv[7], 0, 10);
	const int axis_x = atoi(argv[8]);
	const int hull_y = atoi(argv[9]);
	int w = 0, h = 0;
	if (fscanf(in, "%d %d", &w, &h) != 2 || w <= 0 || h <= 0)
		return 2;
	fprintf(out, "%d %d\n", w, h);
	for (int y = 0; y < h; y++)
		for (int x = 0; x < w; x++)
		{
			unsigned gl = 0, fg = 0, bk = 0, avg = 0;
			if (fscanf(in, "%u %u %u %u", &gl, &fg, &bk, &avg) != 4)
				return 2;
			uint8_t g8 = (uint8_t)gl, f8 = (uint8_t)fg, b8 = (uint8_t)bk;
			const int row = hull_y - 1 - y;
			const RaptureBeamCell c = RaptureBeamCellAt(x - axis_x, row, H, len01, tick, seed);
			RaptureBeamShade(&g8, &f8, &b8, (int)avg, c);
			fprintf(out, "%u %u %u\n", (unsigned)g8, (unsigned)f8, (unsigned)b8);
		}
	fclose(in);
	fclose(out);
	return 0;
}

int main(int argc, char** argv)
{
	if (argc > 1 && strcmp(argv[1], "--shade-grid") == 0)
		return ShadeGrid(argc, argv);
	TestClosedAndGrowth();
	TestConeShape();
	TestDeterminismAndFlicker();
	TestMotes();
	TestFootprintAndShadowRing();
	TestHullShadow();
	TestYellowOnAnyGround();
	TestShadeDetailAndRing();
	TestCellBudget();
	if (g_failures)
	{
		fprintf(stderr, "rapture beam: %d check(s) failed\n", g_failures);
		return 1;
	}
	printf("PASS: rapture beam (yellow cone %.1f->%.1f cells, %d motes, footprint + shadow ring)\n",
	       (double)RAPTURE_BEAM_HULL_HALF_WIDTH, (double)RAPTURE_BEAM_GROUND_HALF_WIDTH,
	       RAPTURE_BEAM_MOTES);
	return 0;
}
