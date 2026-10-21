// =============================================================================
// Weather System — Snow particles and compositing
// =============================================================================
// Created by Phase 17 Plan 01. Wired into game.cpp by Plan 02, asciiid.cpp
// by Plan 03.
//
// All timestamps are uint64_t microseconds (matching game.cpp convention).
// Renderer is opaque — we use our own siv::PerlinNoise for wind variation.
// =============================================================================

#include "weather.h"
#include "render.h"
#include "fast_rand.h"

#include <stdlib.h>
#include <string.h>
#include <math.h>

// ---------------------------------------------------------------------------
// Global
// ---------------------------------------------------------------------------
Weather* weather = 0;

// ---------------------------------------------------------------------------
// Static tables
// ---------------------------------------------------------------------------

// Intensity target per weather state
static const float state_intensity[4] = { 0.0f, 0.3f, 0.7f, 1.0f };

// Particles spawned per second at each state
static const int spawn_rate[4] = { 0, 10, 30, 60 }; // Much less snow

// CP437 snow glyphs: * + . ,
static const uint8_t snow_glyphs[4] = { 0x2A, 0x2B, 0x2E, 0x2C };
static const float snow_speeds[4] = { 15.0f, 12.0f, 9.0f, 6.0f }; // Very fast fall // Much faster fall

// ---------------------------------------------------------------------------
// Create / Delete
// ---------------------------------------------------------------------------
Weather* CreateWeather()
{
	if (weather)
		return weather;

	Weather* w = (Weather*)calloc(1, sizeof(Weather));
	w->state = CLEAR;
	w->intensity = 0.0f;
	w->target_intensity = 0.0f;
	w->transition_speed = 0.1f;
	w->snow_line = 10000.0f;
	w->stamp = 0;
	w->pn_time = 0.0;
	w->_player_x = 0.0f;
	w->_player_y = 0.0f;
	w->_player_z = 0.0f;
	w->pool.count = 0;
	w->pool.head = 0;
	// siv::PerlinNoise default-constructs with default seed
	weather = w;
	return weather;
}

void DeleteWeather(Weather* w)
{
	if (!w)
		return;
	free(w);
	if (weather == w)
		weather = 0;
}

// ---------------------------------------------------------------------------
// Set / Get
// ---------------------------------------------------------------------------
void SetWeather(int state)
{
	if (!weather)
		return;
	if (state < 0) state = 0;
	if (state > 3) state = 3;
	weather->state = (WeatherState)state;
	weather->target_intensity = state_intensity[state];
}

int GetWeather()
{
	if (!weather)
		return 0;
	return (int)weather->state;
}

// ---------------------------------------------------------------------------
// SpawnParticle (static) — ring buffer insert
// ---------------------------------------------------------------------------
static void SpawnParticle(Weather* w, uint64_t stamp)
{
	ParticlePool& pool = w->pool;
	int idx = pool.head;
	pool.head = (pool.head + 1) % ParticlePool::CAPACITY;
	if (pool.count < ParticlePool::CAPACITY)
		pool.count++;

	Particle& p = pool.particles[idx];

	// Random position around player - wide area
	// Spawn in a cylinder from player.z + 50 to player.z + 200 (above player)
	float rx = ((float)fast_rand() / (float)FAST_RAND_MAX - 0.5f) * 100.0f;  // Wider
	float ry = ((float)fast_rand() / (float)FAST_RAND_MAX - 0.5f) * 100.0f;  // Wider
	float rz = 50.0f + ((float)fast_rand() / (float)FAST_RAND_MAX) * 150.0f;  // 50-200 above player

	p.pos[0] = w->_player_x + rx;
	p.pos[1] = w->_player_y + ry;
	p.pos[2] = w->_player_z + rz;

	// Apply weather wind to horizontal drift.
	p.vel[0] = w->wind[0];
	p.vel[1] = w->wind[1];
	p.vel[2] = 0.0f;

	p.birth = stamp;
	// Lifetime 5-8 seconds (slower fall = longer life)
	p.lifetime = (uint64_t)(5000000 + (fast_rand() % 3000001));

	// Random snow glyph with fall speed
	// '*' = fast, '+' = medium, '.' = slow, ',' = slowest
	int glyph_idx = fast_rand() & 3;
	p.glyph = snow_glyphs[glyph_idx];
	p.vel[2] = -snow_speeds[glyph_idx];

	// Foreground: white or light-blue
	if (fast_rand() & 1)
	{
		p.fg[0] = 255; p.fg[1] = 255; p.fg[2] = 255; // white
	}
	else
	{
		p.fg[0] = 200; p.fg[1] = 220; p.fg[2] = 255; // light blue
	}
}

// ---------------------------------------------------------------------------
// UpdateWeather
// ---------------------------------------------------------------------------
void UpdateWeather(uint64_t stamp, float player_x, float player_y, float player_z)
{
	if (!weather)
		return;
	Weather* w = weather;

	// Cache player position
	w->_player_x = player_x;
	w->_player_y = player_y;
	w->_player_z = player_z;

	// Compute dt
	float dt = 0.0f;
	if (w->stamp > 0 && stamp > w->stamp)
		dt = (float)(stamp - w->stamp) * 0.000001f;
	w->stamp = stamp;

	if (dt <= 0.0f || dt > 1.0f)
		return; // first frame or unreasonable gap

	// Intensity lerp toward target
	float diff = w->target_intensity - w->intensity;
	if (fabsf(diff) > 0.001f)
	{
		float step = w->transition_speed * dt;
		if (fabsf(diff) < step)
			w->intensity = w->target_intensity;
		else
			w->intensity += (diff > 0.0f ? step : -step);
	}
	else
	{
		w->intensity = w->target_intensity;
	}

	// Perlin wind variation
	w->pn_time += (double)dt * 0.3;
	w->wind[0] = (float)w->pn.noise(w->pn_time * 0.7, 0.0) * 2.0f * w->intensity;
	w->wind[1] = (float)w->pn.noise(0.0, w->pn_time * 0.7) * 2.0f * w->intensity;

	// Snow line management: lower as intensity increases
	float target_snow_line = 10000.0f - w->intensity * 9000.0f;
	float sl_diff = target_snow_line - w->snow_line;
	if (fabsf(sl_diff) > 1.0f)
		w->snow_line += sl_diff * dt * 0.5f;
	else
		w->snow_line = target_snow_line;

	// Spawn particles based on current state rate
	int rate = spawn_rate[w->state];
	float spawn_count_f = (float)rate * dt * w->intensity;
	int spawn_count = (int)spawn_count_f;
	// Fractional spawn via probability
	float frac = spawn_count_f - (float)spawn_count;
	if (((float)fast_rand() / (float)FAST_RAND_MAX) < frac)
		spawn_count++;

	for (int i = 0; i < spawn_count; i++)
		SpawnParticle(w, stamp);

	// Update particle positions
	ParticlePool& pool = w->pool;
	for (int i = 0; i < pool.count; i++)
	{
		Particle& p = pool.particles[i];
		if (stamp - p.birth > p.lifetime)
			continue; // dead, will be overwritten by ring buffer

		p.pos[0] += (p.vel[0] + w->wind[0]) * dt;
		p.pos[1] += (p.vel[1] + w->wind[1]) * dt;
		p.pos[2] += p.vel[2] * dt;
	}
}

// ---------------------------------------------------------------------------
// CompositeSnowParticles — overlay particles onto AnsiCell buffer
// ---------------------------------------------------------------------------

// Convert RGB to xterm-256 color index (16-231 color cube)
static uint8_t RgbToXterm256(uint8_t r, uint8_t g, uint8_t b)
{
	int ri = (int)r * 5 / 255;
	int gi = (int)g * 5 / 255;
	int bi = (int)b * 5 / 255;
	return (uint8_t)(16 + ri * 36 + gi * 6 + bi);
}

void CompositeSnowParticles(Weather* w, AnsiCell* buf, int width, int height,
                          Renderer* r, uint64_t stamp)
{
	if (!w || !buf || !r)
		return;

	if (w->intensity < 0.01f)
		return;

	ParticlePool& pool = w->pool;
	
	for (int i = 0; i < pool.count; i++)
	{
		Particle& p = pool.particles[i];
		if (stamp < p.birth)
			continue;
		uint64_t age = stamp - p.birth;
		if (age > p.lifetime)
			continue;

		int view[3];
		if (!ProjectCoords(r, p.pos, view))
			continue;

		int sx = view[0];
		int sy = view[1];
		if (sx < 0 || sx >= width || sy < 0 || sy >= height)
			continue;

		// Render varied glyph, keep background
		AnsiCell& cell = buf[sy * width + sx];
		cell.gl = p.glyph;
		cell.fg = RgbToXterm256(p.fg[0], p.fg[1], p.fg[2]);
	}
}

void UpdateSnowAccumulation(Weather* w, Terrain* t, uint64_t stamp)
{
	(void)w;
	(void)t;
	(void)stamp;
	// Terrain material ownership is fail-closed until a dedicated overlay exists.
	// Weather no longer snapshots, mutates, or restores terrain visual-map matids.
}
