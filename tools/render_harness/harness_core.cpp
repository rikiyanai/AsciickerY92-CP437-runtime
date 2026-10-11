// harness_core.cpp — engine setup, per-frame ::Render, cell -> PNG raster.
//
// The engine globals the renderer and loaders expect (terrain, world,
// base_path, mat[]) are owned here, in place of game_app.cpp.

#include "harness.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "sprite.h"
#include "world.h"
#include "terrain.h"
#include "upng.h"

// --- engine globals normally owned by game_app.cpp --------------------------
char base_path[1024] = "";
World* world = nullptr;
Terrain* terrain = nullptr;
extern Material mat[256]; // harness_stubs.cpp

std::vector<HarnessScene>& HarnessSceneRegistry()
{
	static std::vector<HarnessScene> reg;
	return reg;
}

std::string HarnessArg(const HarnessArgs& a, const char* key, const char* def)
{
	auto it = a.find(key);
	return it == a.end() ? std::string(def) : it->second;
}
float HarnessArgF(const HarnessArgs& a, const char* key, float def)
{
	auto it = a.find(key);
	return it == a.end() ? def : (float)atof(it->second.c_str());
}
int HarnessArgI(const HarnessArgs& a, const char* key, int def)
{
	auto it = a.find(key);
	return it == a.end() ? def : atoi(it->second.c_str());
}

std::string Harness::Path(const std::string& rel) const
{
	if (!rel.empty() && rel[0] == '/')
		return rel;
	return repo_root + rel;
}

// Same load sequence as mainmenu.cpp LoadGame(): terrain, 256 materials,
// runtime world (instances + v4 player start), RebuildWorld, terrain dark.
bool Harness::LoadMap(const std::string& a3d_path)
{
	std::string full = Path(a3d_path);
	FILE* f = fopen(full.c_str(), "rb");
	if (!f)
	{
		fprintf(stderr, "[harness] cannot open map %s\n", full.c_str());
		return false;
	}
	PatchIndex* index = 0;
	terrain = LoadTerrain(f, &index);
	if (!terrain)
	{
		fclose(f);
		fprintf(stderr, "[harness] LoadTerrain failed for %s\n", full.c_str());
		return false;
	}
	for (int i = 0; i < 256; i++)
	{
		if (fread(mat[i].shade, 1, sizeof(MatCell) * 4 * 16, f) != sizeof(MatCell) * 4 * 16)
			break;
		mat[i].glyph_plane = NULL; // CP437-only: no material glyph sidecar
	}
	world = LoadWorldRuntime(f);
	fclose(f);
	if (!world)
	{
		fprintf(stderr, "[harness] LoadWorldRuntime failed for %s\n", full.c_str());
		return false;
	}
	RebuildWorld(world, true);

	// mainmenu.cpp runs UpdateTerrainDark per patch with the un-normalised
	// start light {1,0,1,.5}.
	float lt[4] = { 1, 0, 1, 0.5f };
	int n = GetTerrainPatches(terrain);
	for (int i = 0; i < n; i++)
		UpdateTerrainDark(terrain, index + i, world, lt, false);
	FreePatchIndex(index);

	float start[3];
	float yaw, dir;
	if (WorldGetPlayerStart(world, start, &yaw, &dir))
	{
		camera.pos[0] = start[0];
		camera.pos[1] = start[1];
		camera.pos[2] = start[2];
	}
	fprintf(stderr, "[harness] map %s: %d patches\n", full.c_str(), n);
	return true;
}

Sprite* Harness::LoadSpriteXp(const std::string& xp_path, int layer)
{
	std::string full = Path(xp_path);
	// Same call shape as ActorVisualProfileRuntime (detached, quiet, no merge).
	Sprite* s = LoadSpriteLayer(full.c_str(), xp_path.c_str(), layer, 0, true, false, false);
	if (!s)
		fprintf(stderr, "[harness] failed to load sprite %s layer %d\n", full.c_str(), layer);
	else
		fprintf(stderr, "[harness] sprite %s: angles=%d anims=%d projs=%d frames=%d\n",
			xp_path.c_str(), s->angles, s->anims, s->projs, s->frames);
	return s;
}

int Harness::AddActor(const std::string& name, Sprite* s, const float pos[3], float dir, int anim, int frame)
{
	HarnessActor a;
	a.name = name;
	a.sprite = s;
	a.pos[0] = pos[0];
	a.pos[1] = pos[1];
	a.pos[2] = pos[2];
	a.dir = dir;
	a.anim = anim;
	a.frame = frame;
	// Same flags as EnsureLocalPlayerInst() in engine/game.cpp; reps all zero
	// so AnimateSpriteInst() returns the inst's own frame (script-owned).
	int flags = INST_USE_TREE | INST_VISIBLE | INST_VOLATILE;
	int reps[4] = { 0, 0, 0, 0 };
	a.inst = CreateInst(world, s, flags, a.pos, dir, anim, frame, reps, 0, -1);
	actors.push_back(a);
	return (int)actors.size() - 1;
}

HarnessActor* Harness::Actor(const std::string& name)
{
	for (auto& a : actors)
		if (a.name == name)
			return &a;
	return nullptr;
}

float Harness::GroundZ(float x, float y) const
{
	if (!terrain)
		return 0;
	const float patch_size = (float)VISUAL_CELLS; // 8 world units per patch
	int px = (int)floorf(x / patch_size);
	int py = (int)floorf(y / patch_size);
	Patch* p = GetTerrainPatch(terrain, px, py);
	if (!p)
		return 0;
	uint16_t* hm = GetTerrainHeightMap(p);
	int u = (int)floorf((x - px * patch_size) / patch_size * HEIGHT_CELLS + 0.5f);
	int v = (int)floorf((y - py * patch_size) / patch_size * HEIGHT_CELLS + 0.5f);
	if (u < 0) u = 0;
	if (v < 0) v = 0;
	if (u > HEIGHT_CELLS) u = HEIGHT_CELLS;
	if (v > HEIGHT_CELLS) v = HEIGHT_CELLS;
	return (float)hm[v * (HEIGHT_CELLS + 1) + u];
}

void Harness::RenderFrame(int tick)
{
	if (!renderer)
		renderer = CreateRenderer(0);

	for (auto& a : actors)
	{
		if (!a.inst)
			continue;
		int reps[4] = { 0, 0, 0, 0 };
		// The game re-applies the local player inst every frame the same way
		// (game_render_bridge.cpp UpdateSpriteInst(player.player_inst, ...)).
		UpdateSpriteInst(world, a.inst, a.sprite, a.pos, a.dir, a.anim, a.frame, reps);
		if (a.visible)
			ShowInst(a.inst);
		else
			HideInst(a.inst);
	}

	// Game::Render normalises the light by its squared length.
	float lt[4] = { camera.light[0], camera.light[1], camera.light[2], camera.light[3] };
	float n = lt[0] * lt[0] + lt[1] * lt[1] + lt[2] * lt[2];
	if (n > 0.001f)
	{
		lt[0] /= n;
		lt[1] /= n;
		lt[2] /= n;
	}

	cells.assign((size_t)width * height, AnsiCell{});
	uint64_t stamp = (uint64_t)tick * 1000000ull / (uint64_t)(fps > 0 ? fps : 30);
	::Render(renderer, stamp, terrain, world, camera.water, camera.zoom, camera.yaw, camera.pos, lt,
		width, height, cells.data(), nullptr, camera.scene_shift, camera.perspective, frame_input, nullptr);
}

// --- raster -------------------------------------------------------------------

bool HarnessFont::Load(const char* png_path)
{
	upng_t* u = upng_new_from_file(png_path);
	if (!u)
		return false;
	if (upng_decode(u) != UPNG_EOK)
	{
		upng_free(u);
		return false;
	}
	int w = (int)upng_get_width(u);
	int h = (int)upng_get_height(u);
	int comps = (int)upng_get_components(u);
	int bd = (int)upng_get_bitdepth(u);
	if (bd != 8 || w % 16 || h % 16)
	{
		upng_free(u);
		return false;
	}
	const unsigned char* buf = upng_get_buffer(u);
	atlas_w = w;
	cell_w = w / 16;
	cell_h = h / 16;
	mask.resize((size_t)w * h);
	for (int i = 0; i < w * h; i++)
	{
		// Atlas is white glyphs on black; alpha (if present) or luminance.
		const unsigned char* px = buf + (size_t)i * comps;
		mask[i] = (comps == 4 || comps == 2) ? px[comps - 1] : px[0];
	}
	upng_free(u);
	return true;
}

// Pal() from the GL / web cell shaders: xterm cube index -> RGB in 0.2 steps.
static void PalRGB(int p, uint8_t out[3])
{
	int q = p - 16;
	if (q < 0) q = 0;
	if (q > 215) q = 215;
	int hi = q / 36;
	q -= 36 * hi;
	int mid = q / 6;
	int lo = q - 6 * mid;
	out[0] = (uint8_t)(hi * 51);
	out[1] = (uint8_t)(mid * 51);
	out[2] = (uint8_t)(lo * 51);
}

void HarnessRasterize(const HarnessFont& font, const AnsiCell* cells, int w, int h, std::vector<uint8_t>& rgb)
{
	const int cw = font.cell_w, ch = font.cell_h;
	const int iw = w * cw, ih = h * ch;
	rgb.assign((size_t)iw * ih * 3, 0);
	for (int row = 0; row < h; row++)
	{
		// AnsiCell buffers are bottom-up (row 0 = bottom of screen), see
		// Print() in game_app.cpp: buf[x + w*(h-1-y)].
		const AnsiCell* src_row = cells + (size_t)(h - 1 - row) * w;
		for (int col = 0; col < w; col++)
		{
			const AnsiCell& c = src_row[col];
			uint8_t fg[3], bg[3];
			PalRGB(c.fg, fg);
			PalRGB(c.bk, bg);
			int gx = (c.gl & 15) * cw;
			int gy = (c.gl >> 4) * ch;
			for (int y = 0; y < ch; y++)
			{
				const uint8_t* m = font.mask.data() + (size_t)(gy + y) * font.atlas_w + gx;
				uint8_t* dst = rgb.data() + ((size_t)(row * ch + y) * iw + col * cw) * 3;
				for (int x = 0; x < cw; x++)
				{
					int a = m[x];
					dst[x * 3 + 0] = (uint8_t)((fg[0] * a + bg[0] * (255 - a) + 127) / 255);
					dst[x * 3 + 1] = (uint8_t)((fg[1] * a + bg[1] * (255 - a) + 127) / 255);
					dst[x * 3 + 2] = (uint8_t)((fg[2] * a + bg[2] * (255 - a) + 127) / 255);
				}
			}
		}
	}
}

// Minimal PNG writer (stored deflate blocks) — no external deps.
static uint32_t Crc32(const uint8_t* d, size_t n, uint32_t crc = 0)
{
	static uint32_t table[256];
	static bool init = false;
	if (!init)
	{
		for (uint32_t i = 0; i < 256; i++)
		{
			uint32_t c = i;
			for (int k = 0; k < 8; k++)
				c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
			table[i] = c;
		}
		init = true;
	}
	crc = ~crc;
	for (size_t i = 0; i < n; i++)
		crc = table[(crc ^ d[i]) & 0xFF] ^ (crc >> 8);
	return ~crc;
}

static void PutU32(std::vector<uint8_t>& v, uint32_t x)
{
	v.push_back((uint8_t)(x >> 24));
	v.push_back((uint8_t)(x >> 16));
	v.push_back((uint8_t)(x >> 8));
	v.push_back((uint8_t)x);
}

static void Chunk(FILE* f, const char* type, const std::vector<uint8_t>& data)
{
	std::vector<uint8_t> buf;
	PutU32(buf, (uint32_t)data.size());
	buf.insert(buf.end(), type, type + 4);
	buf.insert(buf.end(), data.begin(), data.end());
	uint32_t crc = Crc32(buf.data() + 4, buf.size() - 4);
	PutU32(buf, crc);
	fwrite(buf.data(), 1, buf.size(), f);
}

bool HarnessWritePng(const char* path, const uint8_t* rgb, int w, int h)
{
	FILE* f = fopen(path, "wb");
	if (!f)
		return false;
	static const uint8_t sig[8] = { 137, 80, 78, 71, 13, 10, 26, 10 };
	fwrite(sig, 1, 8, f);

	std::vector<uint8_t> ihdr;
	PutU32(ihdr, (uint32_t)w);
	PutU32(ihdr, (uint32_t)h);
	ihdr.push_back(8); // bit depth
	ihdr.push_back(2); // RGB
	ihdr.push_back(0);
	ihdr.push_back(0);
	ihdr.push_back(0);
	Chunk(f, "IHDR", ihdr);

	std::vector<uint8_t> raw;
	raw.reserve((size_t)(w * 3 + 1) * h);
	for (int y = 0; y < h; y++)
	{
		raw.push_back(0);
		raw.insert(raw.end(), rgb + (size_t)y * w * 3, rgb + (size_t)(y + 1) * w * 3);
	}
	std::vector<uint8_t> z;
	z.push_back(0x78);
	z.push_back(0x01);
	size_t pos = 0;
	uint32_t a = 1, b = 0;
	for (uint8_t c : raw)
	{
		a = (a + c) % 65521;
		b = (b + a) % 65521;
	}
	do
	{
		size_t n = raw.size() - pos;
		if (n > 65535) n = 65535;
		bool last = pos + n == raw.size();
		z.push_back(last ? 1 : 0);
		z.push_back((uint8_t)(n & 0xFF));
		z.push_back((uint8_t)(n >> 8));
		z.push_back((uint8_t)(~n & 0xFF));
		z.push_back((uint8_t)((~n >> 8) & 0xFF));
		z.insert(z.end(), raw.begin() + pos, raw.begin() + pos + n);
		pos += n;
	} while (pos < raw.size());
	PutU32(z, (b << 16) | a);
	Chunk(f, "IDAT", z);
	Chunk(f, "IEND", std::vector<uint8_t>());
	fclose(f);
	return true;
}
