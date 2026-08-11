#pragma once

#include <stdint.h>
#include "PerlinNoise.hpp"

// Forward declarations — do NOT include render.h or terrain.h here.
// weather.cpp includes them for the full definitions.
struct Renderer;
struct Terrain;
struct AnsiCell;
struct Patch;

// ---------------------------------------------------------------------------
// Particle
// ---------------------------------------------------------------------------
struct Particle
{
	float pos[3];
	float vel[3];
	uint64_t birth;
	uint64_t lifetime;
	uint8_t glyph;
	uint8_t fg[3];
};

// ---------------------------------------------------------------------------
// ParticlePool — fixed-capacity ring buffer
// ---------------------------------------------------------------------------
struct ParticlePool
{
	static const int CAPACITY = 512;
	Particle particles[CAPACITY];
	int count;
	int head;
};

// ---------------------------------------------------------------------------
// WeatherState — discrete weather intensities
// ---------------------------------------------------------------------------
enum WeatherState
{
	CLEAR      = 0,
	LIGHT_SNOW = 1,
	HEAVY_SNOW = 2,
	BLIZZARD   = 3
};

// ---------------------------------------------------------------------------
// Weather — top-level weather system state
// ---------------------------------------------------------------------------
struct Weather
{
	WeatherState state;
	float intensity;
	float target_intensity;
	float transition_speed;
	float wind[2];
	float snow_line;
	uint64_t stamp;
	double pn_time;

	// Cached player position (set by UpdateWeather)
	float _player_x;
	float _player_y;
	float _player_z;

	ParticlePool pool;
	siv::PerlinNoise pn;
};

extern Weather* weather;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------
Weather* CreateWeather();
void DeleteWeather(Weather* w);

void UpdateWeather(uint64_t stamp, float player_x, float player_y, float player_z);
void SetWeather(int state);
int  GetWeather();

void CompositeSnowParticles(Weather* w, AnsiCell* buf, int width, int height,
                            Renderer* r, uint64_t stamp);
void UpdateSnowAccumulation(Weather* w, Terrain* t, uint64_t stamp);
