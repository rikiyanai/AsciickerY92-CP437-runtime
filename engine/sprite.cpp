
// =============================================================================
// Sprite Loading & Management (sprite.cpp)
// =============================================================================
//
// WHY: This file is the engine's bridge to the .xp asset pipeline. It owns
// the entire path from compressed .xp file on disk to in-memory Sprite atlas
// ready for render.cpp's BlitSprite/RenderSprite to draw.
//
// .xp Loading Pipeline (full path):
//   1. Open .xp file (gzip-compressed REXPaint format)
//   2. Parse gzip header (ID1=31, ID2=139, CM=8) and skip optional fields
//   3. Decompress deflate payload via tinfl_decompress_mem_to_heap
//   4. Parse decompressed header: version (int32), num_layers (int32)
//   5. Parse per-layer header: width (int32), height (int32)
//   6. Read cells in column-major order: glyph (uint32) + fg RGB (3 bytes) + bk RGB (3 bytes)
//   7. Interpret layer semantics:
//      - Layer 0: Background / Color Key (bk color = transparency key)
//      - Layer 1: Glyph Data — encodes height/ID per cell ('0'-'9','A'-'Z')
//      - Layer 2: Primary visual data (glyphs + colors)
//      - Layer 3+: Swoosh overlay layers (merged onto Layer 2)
//   8. Apply swoosh merging: half-block glyphs (220-223) with cyan (0,255,255)
//      foreground indicate swoosh highlight effects
//   9. Quantize RGB888 colors to 216-color palette indices (16 + 36*r + 6*g + b)
//  10. Assemble multi-angle/multi-frame sprite atlas from the grid layout
//
// Palette Quantization:
//   RGB888 -> 6x6x6 color cube -> xterm-256 palette index.
//   Two paths exist:
//   - RGB2PAL(): Standard quantization with rounding: (component + 25) / 51
//   - LoadSprite inline: Scaled quantization with (component * 5 + 128) / divisor
//     where divisor = 255 (projection) or 400 (reflection, for darkening)
//
// Atlas Assembly:
//   .xp files encode multiple frames in a grid layout. Layer 0's top-left cell
//   encodes the number of view angles. Subsequent column headers encode animation
//   lengths. The grid is: columns = (projections * animation_frames), rows = angles.
//   Each cell in the grid is one sprite frame. Projection and reflection halves
//   are side-by-side when projs=2.
//
// Relationship to Other Modules:
//   - render.cpp: Consumes Sprite::Frame atlas via BlitSprite for screen drawing
//   - game.cpp: Bundle-backed actor/item paths load visuals by V2 asset ids
//   - sprite.h: Defines Sprite struct, Frame, Anim, and AnsiCell layout
//
// Formats:
// - Supports custom Gzipped REXPaint (.xp) files.
// - Handles multi-layer sprite composition:
//   - Layer 0: Background / Color Key
//   - Layer 1: Glyph Data (Height/ID)
//   - Layer 2+: Visual Data (Colors)
//
// Key Features:
// - Automatic "Swoosh" handling: Merges half-block glyphs with background colors.
// - Palette Conversion: Quantizes truecolor inputs to game palette (RGB2PAL).
// - Atlas Generation: Flattens animations and angles into a single texture atlas.
// =============================================================================

// here we're gonna define sprite
// it must havew:
// - one or more frames, each containing:
//   - one or more direction views, each with reflection image

#define _USE_MATH_DEFINES
#include <math.h>
#include <stdlib.h>

#include <string.h>

#include "sprite.h"
#include "sprite_constants.h"
#include "upng.h"
#include "glyph_sidecar.h"
#include "glyph_manifest.h"
#include "glyph_coverage_lookup.h"
#include "glyph_compositor.h"

static Sprite* sprite_head = 0;
static Sprite* sprite_tail = 0;
static bool sprite_note_suppression_shown = false;

static void FreeSpriteFrameStores(Sprite::Frame* atlas, int frames)
{
	if (!atlas)
		return;
	for (int f = 0; f < frames; f++)
	{
		free(atlas[f].cell);
		glyph_plane_free(atlas[f].glyph_plane);
	}
}

struct SpriteInst
{
	Sprite* sprite;
	int pos[3]; // ?
	int anim;
};

/*
Sprite* LoadPlayerSword(const char* path);
Sprite* LoadPlayerShield(const char* path);
Sprite* LoadPlayerSwordShield(const char* path);
Sprite* LoadWolf(const char* path);
Sprite* LoadWolfPlayer(const char* path);
Sprite* LoadWolfPlayerSword(const char* path);
Sprite* LoadWolfPlayerShield(const char* path);
Sprite* LoadWolfPlayerSwordShield(const char* path);
*/

// WHY: Convenience wrapper that loads a player sprite with default recolor table
// and detached=true (not added to the global sprite linked list for editor iteration).
Sprite* LoadPlayer(const char* path)
{
	uint8_t recolor[] = 
	{
		0
		/*
		1, // num of colors 
		170,0,170, 170,0,0, // purple->red shirt
		*/
	};



	Sprite* s = LoadSprite(path, "player", /*true,*/ recolor, true);

	/*
	if (s)
	{
		// detach from sprite list

		if (s->prev)
			s->prev->next = s->next;
		else
			sprite_head = s->next;

		if (s->next)
			s->next->prev = s->prev;
		else
			sprite_tail = s->prev;

		s->prev = 0;
		s->next = 0;
	}
	*/

	return s;
}

// WHY: Reference-counted sprite deallocation. Decrements refs and only frees
// when the last reference is released. Removes from the global sprite linked
// list (sprite_head/sprite_tail) if the sprite was linked (not detached).
void FreeSprite(Sprite* spr)
{
	assert(spr->refs>=1);

	if (spr->refs > 1)
	{
		spr->refs--;
		return;
	}

	if (spr->prev)
		spr->prev->next = spr->next;
	else
	if (spr == sprite_head) // ensure it is not detached sprite from sprite list
		sprite_head = spr->next;

	if (spr->next)
		spr->next->prev = spr->prev;
	else
	if (spr == sprite_tail) // ensure it is not detached sprite from sprite list
		sprite_tail = spr->prev;

	FreeSpriteFrameStores(spr->atlas, spr->frames);

	free(spr->atlas);

	for (int a = 0; a < spr->anims; a++)
		free(spr->anim[a].frame_idx);

	if (spr->name)
		free(spr->name);

	if (spr->glyph_manifest) {
		glyph_manifest_free(spr->glyph_manifest);
		free(spr->glyph_manifest);
		spr->glyph_manifest = 0;
	}

	free(spr);
}

static Sprite* AllocDetachedSprite(int anims, bool recolored, const char* name)
{
	int anim_slots = anims > 0 ? anims : 1;
	Sprite* sprite = (Sprite*)malloc(sizeof(Sprite) + sizeof(Sprite::Anim) * (anim_slots - 1));
	if (!sprite)
		return 0;
	memset(sprite, 0, sizeof(Sprite) + sizeof(Sprite::Anim) * (anim_slots - 1));
	sprite->refs = 1;
	sprite->recolored = recolored;
	if (name)
	{
		size_t len = strlen(name);
		sprite->name = (char*)malloc(len + 1);
		if (!sprite->name)
		{
			free(sprite);
			return 0;
		}
		memcpy(sprite->name, name, len + 1);
	}
	return sprite;
}

static AnsiCell TransparentCellFrom(const AnsiCell* src);

Sprite* CloneSpriteDetached(const Sprite* src, const char* name, bool recolored)
{
	if (!src)
		return 0;

	Sprite* sprite = AllocDetachedSprite(src->anims, recolored, name ? name : src->name);
	if (!sprite)
		return 0;

	memcpy(sprite->proj_bbox, src->proj_bbox, sizeof(sprite->proj_bbox));
	sprite->projs = src->projs;
	sprite->anims = src->anims;
	sprite->frames = src->frames;
	sprite->angles = src->angles;
	sprite->atlas = (Sprite::Frame*)malloc(sizeof(Sprite::Frame) * src->frames);
	if (!sprite->atlas)
	{
		FreeSprite(sprite);
		return 0;
	}
	memset(sprite->atlas, 0, sizeof(Sprite::Frame) * src->frames);

	for (int f = 0; f < src->frames; f++)
	{
		const Sprite::Frame* src_frame = src->atlas + f;
		Sprite::Frame* dst_frame = sprite->atlas + f;
		dst_frame->width = src_frame->width;
		dst_frame->height = src_frame->height;
		memcpy(dst_frame->ref, src_frame->ref, sizeof(dst_frame->ref));
		memcpy(dst_frame->meta_xy, src_frame->meta_xy, sizeof(dst_frame->meta_xy));
		size_t cells = (size_t)src_frame->width * (size_t)src_frame->height;
		dst_frame->cell = (AnsiCell*)malloc(sizeof(AnsiCell) * cells);
		if (!dst_frame->cell)
		{
			FreeSprite(sprite);
			return 0;
		}
		memcpy(dst_frame->cell, src_frame->cell, sizeof(AnsiCell) * cells);
	}

	for (int anim = 0; anim < src->anims; anim++)
	{
		sprite->anim[anim].length = src->anim[anim].length;
		size_t entries = (size_t)2 * (size_t)src->angles * (size_t)src->anim[anim].length;
		sprite->anim[anim].frame_idx = (int*)malloc(sizeof(int) * entries);
		if (!sprite->anim[anim].frame_idx)
		{
			FreeSprite(sprite);
			return 0;
		}
		memcpy(sprite->anim[anim].frame_idx, src->anim[anim].frame_idx, sizeof(int) * entries);
	}

	return sprite;
}

Sprite* CloneSpriteDetachedTransparent(const Sprite* src, const char* name, bool recolored)
{
	Sprite* sprite = CloneSpriteDetached(src, name, recolored);
	if (!sprite)
		return 0;
	for (int f = 0; f < sprite->frames; f++)
	{
		Sprite::Frame* frame = sprite->atlas + f;
		if (!frame->cell)
			continue;
		const size_t cells = (size_t)frame->width * (size_t)frame->height;
		for (size_t i = 0; i < cells; i++)
			frame->cell[i] = TransparentCellFrom(frame->cell + i);
	}
	return sprite;
}

bool SpriteAtlasLayoutMatches(const Sprite* base, const Sprite* overlay)
{
	if (!base || !overlay)
		return false;
	if (base->projs != overlay->projs ||
		base->anims != overlay->anims ||
		base->frames != overlay->frames ||
		base->angles != overlay->angles)
		return false;

	for (int f = 0; f < base->frames; f++)
	{
		const Sprite::Frame* a = base->atlas + f;
		const Sprite::Frame* b = overlay->atlas + f;
		if (a->width != b->width || a->height != b->height)
			return false;
		if (memcmp(a->ref, b->ref, sizeof(a->ref)) != 0)
			return false;
		if (memcmp(a->meta_xy, b->meta_xy, sizeof(a->meta_xy)) != 0)
			return false;
	}

	for (int anim = 0; anim < base->anims; anim++)
	{
		if (base->anim[anim].length != overlay->anim[anim].length)
			return false;
		size_t entries = (size_t)2 * (size_t)base->angles * (size_t)base->anim[anim].length;
		if (memcmp(base->anim[anim].frame_idx, overlay->anim[anim].frame_idx, sizeof(int) * entries) != 0)
			return false;
	}

	return true;
}

static AnsiCell TransparentCellFrom(const AnsiCell* src)
{
	AnsiCell out = *src;
	out.fg = SPRITE_TRANSPARENT_INDEX;
	out.bk = SPRITE_TRANSPARENT_INDEX;
	// FL-4131 (M1 guard): AnsiCell.gl is CP437-only (uint8); 32 = space. Do
	// not extend this to write extended GlyphIds — the parallel GlyphPlane
	// sidecar carries those (see engine/glyph_plane.h, Phase 2). A glyph_id
	// & 0xFF truncation here would silently land on the wrong CP437 glyph.
	out.gl = 32;
	return out;
}

static bool SpriteCellVisible(const AnsiCell* cell)
{
	if (!cell)
		return false;
	return !((cell->bk == SPRITE_TRANSPARENT_INDEX &&
			cell->fg == SPRITE_TRANSPARENT_INDEX) ||
		((cell->gl == 32 || cell->gl == 0) &&
			cell->bk == SPRITE_TRANSPARENT_INDEX) ||
		(cell->gl == 219 &&
			cell->fg == SPRITE_TRANSPARENT_INDEX));
}

static bool SpriteAtlasAnimationTopologyMatches(const Sprite* base, const Sprite* overlay)
{
	if (!base || !overlay)
		return false;
	if (base->projs != overlay->projs ||
		base->anims != overlay->anims ||
		base->frames != overlay->frames ||
		base->angles != overlay->angles)
		return false;

	for (int anim = 0; anim < base->anims; anim++)
	{
		if (base->anim[anim].length != overlay->anim[anim].length)
			return false;
		size_t entries = (size_t)2 * (size_t)base->angles * (size_t)base->anim[anim].length;
		if (memcmp(base->anim[anim].frame_idx, overlay->anim[anim].frame_idx, sizeof(int) * entries) != 0)
			return false;
	}

	return true;
}

Sprite* CreateSpriteDeltaOverlayDetached(const Sprite* base, const Sprite* variant, const char* name)
{
	if (!SpriteAtlasLayoutMatches(base, variant))
		return 0;

	Sprite* overlay = CloneSpriteDetached(variant, name ? name : "overlay", variant ? variant->recolored : false);
	if (!overlay)
		return 0;

	for (int f = 0; f < overlay->frames; f++)
	{
		const Sprite::Frame* base_frame = base->atlas + f;
		Sprite::Frame* overlay_frame = overlay->atlas + f;
		size_t cells = (size_t)overlay_frame->width * (size_t)overlay_frame->height;
		for (size_t i = 0; i < cells; i++)
		{
			if (memcmp(base_frame->cell + i, overlay_frame->cell + i, sizeof(AnsiCell)) == 0)
				overlay_frame->cell[i] = TransparentCellFrom(base_frame->cell + i);
		}
	}

	return overlay;
}

bool MaskSpriteByVisibleCells(Sprite* dst, const Sprite* mask)
{
	if (!SpriteAtlasLayoutMatches(dst, mask))
		return false;

	for (int f = 0; f < dst->frames; f++)
	{
		Sprite::Frame* dst_frame = dst->atlas + f;
		const Sprite::Frame* mask_frame = mask->atlas + f;
		size_t cells = (size_t)dst_frame->width * (size_t)dst_frame->height;
		for (size_t i = 0; i < cells; i++)
		{
			if (SpriteCellVisible(mask_frame->cell + i))
				continue;
			dst_frame->cell[i] = TransparentCellFrom(dst_frame->cell + i);
		}
	}
	return true;
}

void RecolorSpriteInPlace(Sprite* sprite, const uint8_t palette_map_proj[256], const uint8_t palette_map_refl[256], const uint8_t glyph_map[256])
{
	if (!sprite)
		return;

	int cols_per_angle = (sprite->angles > 0) ? (sprite->frames / sprite->angles) : sprite->frames;
	int refl_column = (sprite->projs > 1) ? (cols_per_angle / 2) : cols_per_angle;

	for (int f = 0; f < sprite->frames; f++)
	{
		Sprite::Frame* frame = sprite->atlas + f;
		size_t cells = (size_t)frame->width * (size_t)frame->height;
		int col = cols_per_angle > 0 ? (f % cols_per_angle) : 0;
		bool refl = (sprite->projs > 1) && (col >= refl_column);
		const uint8_t* palette_map = refl ? palette_map_refl : palette_map_proj;
		for (size_t i = 0; i < cells; i++)
		{
			AnsiCell* cell = frame->cell + i;
			cell->fg = palette_map[cell->fg];
			cell->bk = palette_map[cell->bk];
			cell->gl = glyph_map[cell->gl];
		}
	}

	sprite->recolored = true;
}

Sprite* GetFirstSprite(bool all)
{
	Sprite* s = sprite_head;
	if (all)
		return s;
	while (s && s->recolored)
		s=s->next;
	return s;
}

Sprite* GetPrevSprite(Sprite* s, bool all)
{
	if (!s)
		return 0;
	s=s->prev;
	if (all)
		return s;
	while (s && s->recolored)
		s=s->prev;
	return s;
}

Sprite* GetNextSprite(Sprite* s, bool all)
{
	if (!s)
		return 0;
	s=s->next;
	if (all)
		return s;
	while (s && s->recolored)
		s=s->next;
	return s;
}

int GetSpriteName(Sprite* s, char* buf, int size)
{
	if (!s)
	{
		if (buf && size > 0)
			*buf = 0;
		return 0;
	}

	int len = (int)strlen(s->name);

	if (buf && size > 0)
		strncpy(buf, s->name, size);

	return len + 1;
}

void SetSpriteCookie(Sprite* s, void* cookie)
{
	if (s)
		s->cookie = cookie;
}

void* GetSpriteCookie(Sprite* s)
{
	if (!s)
		return 0;
	return s->cookie;
}

// WHY: Converts a truecolor RGB triplet to the nearest 216-color xterm palette index.
// [DATA-CONTRACT:SPRITE] Palette quantization — RGB888 to 216-color cube index
//
// The 216-color cube maps each RGB component (0-255) to a 6-level grid (0-5).
// Quantization formula per component: level = (value + 25) / 51
//   - 51 = 255/5, the step size between adjacent levels
//   - +25 provides rounding to nearest level (half-step = 51/2 ~ 25)
//   - Result: 0-255 maps to 0-5
//
// Palette index formula: 16 + 36*r + 6*g + b
//   - 16: offset past the 16 standard ANSI colors (indices 0-15)
//   - 36*r: red stride — each red level spans 6*6 = 36 entries
//   - 6*g: green stride — each green level spans 6 entries
//   - b: blue component (least significant)
//   - Produces indices 16-231 in the xterm-256 palette
//   - Indices 232-255 would be a grayscale ramp (unused here)
//
// NOTE: LoadSprite uses a slightly different inline formula for quantization
// that applies a divisor (255 or 400) to control projection vs reflection brightness.
// TODO(PIPELINE-FIX): The two quantization paths (RGB2PAL vs LoadSprite inline)
// use different rounding strategies. RGB2PAL rounds to nearest; LoadSprite's inline
// formula uses (component * 5 + 128) / divisor. These should be unified or the
// divergence explicitly justified.
int RGB2PAL(const uint8_t* rgb)
{
	int r = (rgb[0] + 25) / 51;
	int g = (rgb[1] + 25) / 51;
	int b = (rgb[2] + 25) / 51;
	return 16 + 36 * r + 6 * g + b;
}

// WHY: Inverse of RGB2PAL — converts a 216-color palette index back to RGB888.
// Reverses the 16 + 36*r + 6*g + b encoding by extracting each component
// via integer division and modulo, then scaling each level (0-5) back to
// 0-255 range by multiplying by 51. Note: this does NOT round-trip perfectly
// with RGB2PAL because the original rounding in RGB2PAL is lossy.
void PAL2RGB(int pal, uint8_t* rgb)
{
	pal -= 16;
	int r = pal / 36;
	pal -= r * 36;
	int g = pal / 6;
	pal -= g * 6;
	int b = pal;
	rgb[0] = r * 51;
	rgb[1] = g * 51;
	rgb[2] = b * 51;
}

int AverageGlyphTransp(const AnsiCell* ptr, int mask);

extern "C" void *tinfl_decompress_mem_to_heap(const void *pSrc_buf, size_t src_buf_len, size_t *pOut_len, int flags);

// WHY: Main .xp loading entry point. Parses gzip-compressed REXPaint files,
// interprets multi-layer sprite data, applies swoosh merging, quantizes colors,
// and assembles a Sprite atlas. This is the sole path from .xp files into the engine.
static bool SpriteVerboseNotes()
{
	static int cached = -1;
	if (cached == -1)
		cached = getenv("ASCIICKICKER_SPRITE_VERBOSE") ? 1 : 0;
	return cached != 0;
}

Sprite* LoadSprite(const char* path, const char* name, /*bool has_refl,*/ const uint8_t* recolor, bool detached, bool quiet_failures)
{
	return LoadSpriteLayer(path, name, 2, recolor, detached, quiet_failures, true);
}

Sprite* LoadSpriteLayer(const char* path, const char* name, int visual_layer_index, const uint8_t* recolor, bool detached, bool quiet_failures, bool merge_extra_layers)
{
	// [DATA-CONTRACT:SPRITE] .xp format entry point — gzip-compressed REXPaint file
	//
	// .xp Binary Format (REXPaint-compatible, gzip-compressed):
	//
	// On-disk: standard gzip container (RFC 1952)
	//   [GZip header: ID1=31, ID2=139, CM=8, FLG, MTIME, XFL, OS]
	//   [Optional: FEXTRA, FNAME, FCOMMENT, FHCRC fields]
	//   [Deflate-compressed payload]
	//   [CRC32 + ISIZE trailer (8 bytes)]
	//
	// Decompressed payload (little-endian):
	//   int32: version (offset 0, currently skipped/unused)
	//   int32: num_layers (offset 4)
	//   int32: width (offset 8, applies to ALL layers)
	//   int32: height (offset 12, applies to ALL layers)
	//
	//   Per layer (num_layers times):
	//     int32: layer_width (offset: start of layer)
	//     int32: layer_height (offset: start of layer + 4)
	//     Per cell (width * height cells, COLUMN-MAJOR order):
	//       uint32: glyph (4 bytes — code point, typically 0-255 for CP437)
	//       uint8:  fg_r (1 byte — foreground red)
	//       uint8:  fg_g (1 byte — foreground green)
	//       uint8:  fg_b (1 byte — foreground blue)
	//       uint8:  bk_r (1 byte — background red)
	//       uint8:  bk_g (1 byte — background green)
	//       uint8:  bk_b (1 byte — background blue)
	//     Total per cell: 10 bytes (4 glyph + 3 fg + 3 bk)
	//
	// NOTE: Width and height in the global header (offsets 8,12) are used for all
	// layers. Each layer also has its own width/height pair (the 2-int gap between
	// layer data blocks), but this code reads width/height only once from the
	// global header and skips the per-layer copies by pointer arithmetic.
	//
	// TODO(PIPELINE-FIX): The per-layer width/height fields are skipped without
	// validation. If an .xp file has layers with differing dimensions (which
	// REXPaint doesn't produce but is technically valid), this code would misparse.
	//
	// Layer semantics (game-specific, not part of REXPaint spec):
	// - Layer 0: Background / Color Key — bk color defines transparency
	// - Layer 1: Glyph Data — glyph encodes height ('0'-'9' = 0-9, 'A'-'Z' = 10-35)
	// - Layer 2: Primary visual data (glyphs + colors for rendering)
	// - Layer 3+: Swoosh overlay layers (merged onto Layer 2 with special rules)

	if (!detached && !recolor && visual_layer_index == 2 && merge_extra_layers)
	{
		// first, lookup linked sprites, return pointer to already loaded one if found
		Sprite* s = GetFirstSprite();
		while (s)
		{
			if (strcmp(s->name, name) == 0)
			{
				s->refs++;
				return s;
			}
			s = s->next;
		}
	}

	FILE* f = fopen(path, "rb");
	if (!f)
	{
		if (!quiet_failures)
			fprintf(stderr, "[SPRITE] %s: file not found\n", path);
		return 0;
	}

	// [FLOW:PIPELINE] Warn when loading WIP sprites from staging directory
	// WHY: Sprites in staging/ are work-in-progress and should not be used
	// in production maps. This warning alerts users they may be using
	// unpublished assets. The sprite still loads - this is informational only.
	if (strstr(path, "staging/") != nullptr || strstr(path, "staging\\") != nullptr)
	{
		if (!quiet_failures)
			fprintf(stderr, "[STAGING] Loading WIP sprite: %s\n", path);
	}

	/////////////////////////////////
	// [DATA-CONTRACT:SPRITE] Gzip header parsing — validates ID1=31, ID2=139, CM=8
	// WHY: .xp files are always gzip-compressed. We parse the header manually
	// rather than using zlib because only the deflate payload is needed, and
	// tinfl provides a minimal decompressor without external dependencies.
	// TODO(PIPELINE-FIX): No validation of gzip header flags beyond the ones
	// checked. A corrupt or non-gzip file that happens to start with 31,139,8
	// will proceed to decompression and likely crash in tinfl.

	struct GZ
	{
		uint8_t id1, id2, cm, flg;
		uint8_t mtime[4];
		uint8_t xfl, os;
	};

	GZ gz;
	int r;
	r=(int)fread(&gz, 10, 1, f);
	if (r != 1)
	{
		if (!quiet_failures)
			fprintf(stderr, "[SPRITE] %s: failed to read gzip header\n", path);
		fclose(f);
		return 0;
	}

	/*
	assert(gz.id1 == 31 && gz.id2 == 139 && "gz identity");
	assert(gz.cm == 8 && "deflate method");
	*/

	if (gz.id1 != 31 || gz.id2 != 139 || gz.cm != 8)
	{
		if (!quiet_failures)
			fprintf(stderr, "[SPRITE] %s: invalid gzip header (id1=%d id2=%d cm=%d, expected 31 139 8)\n", path, gz.id1, gz.id2, gz.cm);
		fclose(f);
		return 0;
	}

	if (gz.flg & (1 << 2/*FEXTRA*/))
	{
		int hi, lo;
		r=(int)fread(&hi, 1, 1, f);
		r=(int)fread(&lo, 1, 1, f);

		int len = (hi << 8) | lo;
		fseek(f, len, SEEK_CUR);
	}

	if (gz.flg & (1 << 3/*FNAME*/))
	{
		uint8_t ch;
		do
		{
			ch = 0;
			r=(int)fread(&ch, 1, 1, f);
		} while (ch);
	}

	if (gz.flg & (1 << 4/*FCOMMENT*/))
	{
		uint8_t ch;
		do
		{
			ch = 0;
			r=(int)fread(&ch, 1, 1, f);
		} while (ch);
	}

	if (gz.flg & (1 << 1/*FFHCRC*/))
	{
		uint16_t crc;
		r=(int)fread(&crc, 2, 1, f);
	}

	// deflated data blocks ...
	// read everything till end of file, trim tail by 8 bytes (crc32,isize)

	long now = ftell(f);
	fseek(f, 0, SEEK_END);

	unsigned long insize = ftell(f) - now - 8;
	unsigned char* in = (unsigned char*)malloc(insize);
	fseek(f, now, SEEK_SET);

	r=(int)fread(in, 1, insize, f);


	size_t out_size=0;
	void* out = tinfl_decompress_mem_to_heap(in, insize, &out_size, 0);
	// void* out = u_inflate(in, insize);
	free(in);

	/////////////////////////////////
	// GZ OUTRO:

	uint32_t crc32, isize;
	r=(int)fread(&crc32, 4, 1, f);
	r=(int)fread(&isize, 4, 1, f);
	fclose(f);

	// Validate decompression succeeded
	if (!out)
	{
		if (!quiet_failures)
			fprintf(stderr, "[SPRITE] %s: gzip decompression failed\n", path);
		return 0;
	}
	if (isize != out_size)
	{
		if (!quiet_failures)
			fprintf(stderr, "[SPRITE] %s: decompressed size mismatch (expected %u, got %zu)\n", path, isize, out_size);
		free(out);
		return 0;
	}

	/////////////////////////////////
	// [DATA-CONTRACT:SPRITE] .xp header parsing — version, layers, width, height
	// WHY: The decompressed .xp payload starts with 4 int32 values. We skip
	// version (offset 0) and read layers, width, height. Minimum 3 layers
	// are required because our layer semantics need L0 (color key), L1 (height),
	// and L2 (visual).
	// TODO(PIPELINE-FIX): layers < 3 silently returns null. No error message or
	// logging to help diagnose bad .xp files. Also, width and height are not
	// bounds-checked against reasonable limits (e.g., could be negative if file
	// is corrupt, causing massive malloc).

	int layers = *((int*)out + 1);
	int width = *((int*)out + 2);
	int height = *((int*)out + 3);

	// Validate layer count
	if (layers < SPRITE_MIN_LAYERS)
	{
		if (!quiet_failures)
			fprintf(stderr, "[SPRITE] %s: layer count %d, expected >= %d\n", path, layers, SPRITE_MIN_LAYERS);
		free(out);
		return 0;
	}
	if (visual_layer_index < 2 || visual_layer_index >= layers)
	{
		if (!quiet_failures)
			fprintf(stderr, "[SPRITE] %s: visual layer index %d out of range (layers=%d)\n", path, visual_layer_index, layers);
		free(out);
		return 0;
	}

	// Validate dimensions
	if (width < 1 || height < 1)
	{
		if (!quiet_failures)
			fprintf(stderr, "[SPRITE] %s: invalid dimensions (width=%d height=%d)\n", path, width, height);
		free(out);
		return 0;
	}

	// Note about extra layers (per internal design notes: layers 4+ exist but won't be used)
	if (layers > SPRITE_MIN_LAYERS)
	{
		if (!quiet_failures && SpriteVerboseNotes())
			fprintf(stderr, "[SPRITE] %s: note: %d layers found, only first 3 used (layers 4+ merged as swoosh)\n", path, layers);
		else if (!quiet_failures && !SpriteVerboseNotes() && !sprite_note_suppression_shown)
		{
			sprite_note_suppression_shown = true;
			fprintf(stderr, "[SPRITE] note: extra sprite layer notes suppressed; set ASCIICKICKER_SPRITE_VERBOSE=1 for per-file detail\n");
		}
	}

	// [DATA-CONTRACT:SPRITE] XPCell binary structure — 10 bytes per cell
	// WHY: Matches the REXPaint on-disk cell format exactly. #pragma pack(push,1)
	// ensures no padding between fields so we can cast directly from the
	// decompressed buffer without per-field reads.
	//   uint32_t glyph: 4 bytes — CP437 code point (0-255 typical, uint32 for alignment)
	//   uint8_t fg[3]:  3 bytes — foreground RGB888
	//   uint8_t bk[3]:  3 bytes — background RGB888
	// Total: 10 bytes per cell, packed, no padding
	// TODO(PIPELINE-FIX): glyph is uint32 but only values 0-255 are meaningful
	// for CP437. Values >255 are silently accepted and will index out of bounds
	// in glyph_coverage[256] lookups later (undefined behavior).
#pragma pack(push,1)
	struct XPCell
	{
		uint32_t glyph;
		uint8_t fg[3];
		uint8_t bk[3];

		int GetDigit() const
		{
			int digit = -1;
			if (glyph >= '0' && glyph <= '9')
				digit = glyph - '0';
			else
			if (glyph >= 'A' && glyph <= 'Z')
				digit = glyph + 0xA - 'A';
			else
			if (glyph >= 'a' && glyph <= 'z')
				digit = glyph + 0xa - 'a';

			return digit;
		}
	};
#pragma pack(pop)

	// [DATA-CONTRACT:SPRITE] Layer pointer arithmetic — column-major, 2-int gap between layers
	// WHY: Each layer's cell data is preceded by a 2-int (8-byte) header containing
	// per-layer width and height. We skip these via the +2 offset when jumping
	// between layer data blocks. Layer 0 starts at offset 16 (after 4 ints of
	// global header). Cells are in column-major order (x varies slowest).
	int cells = width * height;
	XPCell* layer0 = (XPCell*)((int*)out + 4); // bg specifies color key
	XPCell* layer1 = (XPCell*)((int*)(layer0 + cells) + 2); // glyph specifies height + '0'
	XPCell* layer2 = (XPCell*)((int*)(layer1 + cells) + 2); // image map
	XPCell* visual_layer = layer2;
	XPCell* layer_cursor = layer2;
	for (int layer_i = 3; layer_i <= visual_layer_index; layer_i++)
	{
		layer_cursor = (XPCell*)((int*)(layer_cursor + cells) + 2);
		visual_layer = layer_cursor;
	}

	// [VALIDATION] Glyph range check - VAL-03 + FL-4131 Phase 2 extended admission
	// WHY: Glyph values > 255 would index out of bounds in glyph_coverage[256].
	// This catches corrupt .xp files before they cause undefined behavior.
	//
	// FL-4131 Phase 2 backend admission:
	//   If a valid glyph sidecar exists and declares extended glyphs (>255),
	//   load the referenced manifest, verify its SHA-256, check admission_set
	//   and coverage for every extended glyph, then populate GlyphPlane.cells.
	//   Any verification failure is fail-closed with an explicit [FL-4131] log.
	//   AnsiCell.gl stays CP437-only: extended glyphs store 0x3F ('?') as a visible
	//   fallback byte until Phase 3 renderer manifest lookup lands.
	//
	//   Legacy path (no sidecar): original VAL-03 rejection preserved unchanged.
	//   CP437-only sidecar path: metadata handoff unchanged (has_sidecar_for_frames).
	//
	//   Hard boundary: GLYPH_ID_NONE and GLYPH_ID_UNRESOLVED sentinels are
	//   non-renderable and rejected by both paths.
	bool has_sidecar_for_frames = false;
	GlyphManifest* manifest = NULL; // heap-allocated manifest for extended-glyph sprites
	if (glyph_sidecar_exists(path))
	{
		// Sidecar exists — probe and validate it.
		GlyphSidecar sc;
		char sc_errbuf[512];
		GlyphSidecarError sc_rc = glyph_sidecar_parse_for_xp(path, &sc, sc_errbuf, (int)sizeof(sc_errbuf));
		if (sc_rc != GLYPH_SIDECAR_OK)
		{
			if (!quiet_failures)
				fprintf(stderr, "[SPRITE] %s: glyph sidecar invalid (%s): %s\n",
						path, glyph_sidecar_error_name(sc_rc), sc_errbuf);
			free(out);
			return 0;
		}
		// Valid sidecar — check whether any cell actually uses an extended glyph.
		bool has_extended_glyph = false;
		for (int c = 0; c < cells && !has_extended_glyph; c++)
		{
			if (layer0[c].glyph > 255 || layer1[c].glyph > 255 || visual_layer[c].glyph > 255)
				has_extended_glyph = true;
		}
		if (has_extended_glyph)
		{
			// Resolve manifest path from sidecar (explicit or default).
			char manifest_path[1024];
			if (sc.has_glyph_manifest_path && sc.glyph_manifest_path[0]) {
				snprintf(manifest_path, sizeof(manifest_path), "%s", sc.glyph_manifest_path);
			} else {
				snprintf(manifest_path, sizeof(manifest_path), "assets/glyphs/fixtures/%s.json", sc.content_pack_id);
			}

			// Load manifest and verify SHA-256.
			manifest = (GlyphManifest*)malloc(sizeof(GlyphManifest));
			if (!manifest) {
				if (!quiet_failures)
					fprintf(stderr, "[FL-4131] %s: out of memory allocating manifest\n", path);
				free(out);
				return 0;
			}
			char man_err[512];
			GlyphManifestError man_rc = glyph_manifest_load_and_verify(
					manifest_path, sc.glyph_manifest_hash, manifest, man_err, sizeof(man_err));
			if (man_rc != GLYPH_MANIFEST_OK) {
				if (!quiet_failures)
					fprintf(stderr, "[FL-4131] %s: manifest load failed (%s): %s (path=%s)\n",
						path, glyph_manifest_error_name(man_rc), man_err, manifest_path);
				free(manifest);
				free(out);
				return 0;
			}

			// Admission + coverage check for every extended glyph in all layers.
			for (int c = 0; c < cells; c++) {
				GlyphId gids[3] = { layer0[c].glyph, layer1[c].glyph, visual_layer[c].glyph };
				for (int gi = 0; gi < 3; gi++) {
					GlyphId g = gids[gi];
					if (glyph_id_is_legacy_cp437(g)) continue;
					if (glyph_id_is_sentinel(g)) {
						if (!quiet_failures)
							fprintf(stderr, "[FL-4131] %s: sentinel glyph %u in layer at cell %d\n", path, g, c);
						glyph_manifest_free(manifest);
						free(manifest);
						free(out);
						return 0;
					}
					if (!glyph_manifest_is_admitted(manifest, g)) {
						if (!quiet_failures)
							fprintf(stderr, "[FL-4131] %s: glyph %u not admitted by manifest (cell %d)\n", path, g, c);
						glyph_manifest_free(manifest);
						free(manifest);
						free(out);
						return 0;
					}
					uint16_t cov;
					if (!glyph_manifest_lookup_coverage(manifest, g, &cov)) {
						if (!quiet_failures)
							fprintf(stderr, "[FL-4131] %s: glyph %u missing coverage in manifest (cell %d)\n", path, g, c);
						glyph_manifest_free(manifest);
						free(manifest);
						free(out);
						return 0;
					}
				}
			}
			has_sidecar_for_frames = true;
		}
		else
		{
			// Sidecar present, all glyphs are CP437 — metadata handoff (Phase 1).
			// Legacy CP437 processing is unchanged.
			has_sidecar_for_frames = true;
		}
	}
	else
	{
		// No sidecar — legacy VAL-03 path (gate: val03_legacy_gate_preserved).
		for (int c = 0; c < cells; c++)
		{
			if (layer0[c].glyph > 255 || layer1[c].glyph > 255 || visual_layer[c].glyph > 255)
			{
				if (!quiet_failures)
					fprintf(stderr, "[SPRITE] %s: glyph out of range at cell %d (L0=%u L1=%u L%d=%u, max=255)\n",
						path, c, layer0[c].glyph, layer1[c].glyph, visual_layer_index, visual_layer[c].glyph);
				free(out);
				return 0;
			}
		}
	}

	// [DATA-CONTRACT:SPRITE] Swoosh layer merging — layers 3+ merged onto layer 2
	// [DATA-CONTRACT:SPRITE] Swoosh layer merging — layers 3+ merged onto layer 2
	// WHY: Layers above 2 are "swoosh" overlays that add highlight/motion effects.
	// The last layer (layers-1) has special swoosh semantics when fg is cyan (0,255,255).
	// Non-swoosh cells from higher layers simply overwrite layer 2 unless their bk
	// is magenta (255,0,255) which is REXPaint's transparency convention.
	//
	// Swoosh rules for half-block glyphs (220=lower, 221=left, 222=right, 223=upper):
	//   - If swoosh bk is transparent: average the underlying cell's coverage under
	//     the swoosh mask, lighten the fg, preserve the underlying bk
	//   - If swoosh bk is opaque: average only the fg portion, set bk to swoosh's bk
	// For full-block glyphs (default case):
	//   - If underlying is fully transparent: replace entirely with swoosh cell
	//   - Otherwise: lighten each non-transparent component by +51 per channel
	//
	// Color constants are defined in sprite_constants.h:
	//   - Cyan (SPRITE_CYAN_*) = swoosh marker
	//   - Magenta (SPRITE_MAGENTA_*) = REXPaint transparency
	//   - Palette indices: SPRITE_SWOOSH_INDEX (254), SPRITE_TRANSPARENT_INDEX (255)
	// TODO(PIPELINE-FIX): Swoosh merging only activates on the LAST layer (m == layers-1).
	//   All other layers above 2 are simple overwrites. This assumes a specific layer
	//   ordering convention that is undocumented in the .xp files themselves.
	XPCell* merge = layer2;
	for (int m = 3; merge_extra_layers && m < layers; m++)
	{
		merge = (XPCell*)((int*)(merge + cells) + 2);
		for (int c = 0; c < cells; c++)
		{
			if (m == layers-1 && merge[c].fg[0] == SPRITE_CYAN_R && merge[c].fg[1] == SPRITE_CYAN_G && merge[c].fg[2] == SPRITE_CYAN_B)
			{
				bool fg_transp =
					layer2[c].fg[0] == layer0[c].bk[0] &&
					layer2[c].fg[1] == layer0[c].bk[1] &&
					layer2[c].fg[2] == layer0[c].bk[2];
				bool bk_transp =
					layer2[c].bk[0] == layer0[c].bk[0] &&
					layer2[c].bk[1] == layer0[c].bk[1] &&
					layer2[c].bk[2] == layer0[c].bk[2];

				if (layer2[c].bk[0] == SPRITE_MAGENTA_R && layer2[c].bk[1] == SPRITE_MAGENTA_G && layer2[c].bk[2] == SPRITE_MAGENTA_B)
				{
					fg_transp = true;
					bk_transp = true;
				}

				bool swoosh_bk_transp =
					merge[c].bk[0] == layer0[c].bk[0] &&
					merge[c].bk[1] == layer0[c].bk[1] &&
					merge[c].bk[2] == layer0[c].bk[2];

				// if bg is also swoosh, unify to full block
				if (merge[c].bk[0] == SPRITE_CYAN_R && merge[c].bk[1] == SPRITE_CYAN_G && merge[c].bk[2] == SPRITE_CYAN_B)
					merge[c].glyph = SPRITE_GLYPH_FULL_BLOCK;

				int mask = 0;
				switch (merge[c].glyph)
				{
					case 0: // SPRITE_GLYPH_NULL
					case 32: // SPRITE_GLYPH_SPACE
						if (merge[c].bk[0] != SPRITE_MAGENTA_R || merge[c].bk[1] != SPRITE_MAGENTA_G || merge[c].bk[2] != SPRITE_MAGENTA_B)
							layer2[c] = merge[c];
						break;

					case 220: // SPRITE_GLYPH_HALF_LOWER
						if (!mask)
							mask = SPRITE_MASK_LOWER;
					case 221: // SPRITE_GLYPH_HALF_LEFT
						if (!mask)
							mask = SPRITE_MASK_LEFT;
					case 222: // SPRITE_GLYPH_HALF_RIGHT
						if (!mask)
							mask = SPRITE_MASK_RIGHT;
					case 223: // SPRITE_GLYPH_HALF_UPPER
						if (!mask)
							mask = SPRITE_MASK_UPPER;

						if (swoosh_bk_transp)
						{
							// if halfblock with background transparent
							AnsiCell ac;
							ac.gl = 0; // temp AnsiCell.gl unused when routing through AverageGlyphTranspForId
							ac.fg = fg_transp ? SPRITE_TRANSPARENT_INDEX : RGB2PAL(layer2[c].fg);
							ac.bk = bk_transp ? SPRITE_TRANSPARENT_INDEX : RGB2PAL(layer2[c].bk);

							// - calc 2 averages under swoosh fg and under swoosh bg
							int fg = AverageGlyphTranspForId((GlyphId)layer2[c].glyph, ac.fg, ac.bk, mask);
							int bk = AverageGlyphTranspForId((GlyphId)layer2[c].glyph, ac.fg, ac.bk, SPRITE_MASK_FULL ^ mask);

							if (fg == SPRITE_TRANSPARENT_INDEX)
							{
								// - if fg average is transparent set fg to swoosh color
								layer2[c].fg[0] = SPRITE_CYAN_R;
								layer2[c].fg[1] = SPRITE_CYAN_G;
								layer2[c].fg[2] = SPRITE_CYAN_B;
							}
							else
							{
								//   otherwise set fg to lighten fg average
								PAL2RGB(LightenColor(fg), layer2[c].fg);
							}

							if (fg == SPRITE_TRANSPARENT_INDEX)
							{
								// - if bk average is transparent set bk to transparent
								layer2[c].bk[0] = layer0[c].bk[0];
								layer2[c].bk[1] = layer0[c].bk[1];
								layer2[c].bk[2] = layer0[c].bk[2];
							}
							else
							{
								//   otherwise set bk to bk average 
								PAL2RGB(bk, layer2[c].bk);
							}

							// - set glyph to swoosh glyph
							layer2[c].glyph = merge[c].glyph;
						}
						else
						{
							// if halfblock with background opaque
							AnsiCell ac;
							ac.gl = 0; // temp AnsiCell.gl unused when routing through AverageGlyphTranspForId
							ac.fg = fg_transp ? SPRITE_TRANSPARENT_INDEX : RGB2PAL(layer2[c].fg);
							ac.bk = bk_transp ? SPRITE_TRANSPARENT_INDEX : RGB2PAL(layer2[c].bk);

							// - calc only average under swoosh fg
							int fg = AverageGlyphTranspForId((GlyphId)layer2[c].glyph, ac.fg, ac.bk, mask);

							// - if fg average is transparent set fg to swoosh color
							if (fg == SPRITE_TRANSPARENT_INDEX)
							{
								// - if fg average is transparent set fg to swoosh color
								layer2[c].fg[0] = SPRITE_CYAN_R;
								layer2[c].fg[1] = SPRITE_CYAN_G;
								layer2[c].fg[2] = SPRITE_CYAN_B;
							}
							else
							{
								//   otherwise set fg to lighten fg average
								PAL2RGB(LightenColor(fg), layer2[c].fg);
							}

							// - set bk to swoosh bk
							layer2[c].bk[0] = merge[c].bk[0];
							layer2[c].bk[1] = merge[c].bk[1];
							layer2[c].bk[2] = merge[c].bk[2];

							// - set glyph to swoosh glyph
							layer2[c].glyph = merge[c].glyph;
						}

						break;

					default:
						// if fullblock

						if (fg_transp && bk_transp)
						{
							layer2[c] = merge[c];
						}
						else
						{
							if (fg_transp)
							{
								// set fg to swoosh color
								layer2[c].fg[0] = SPRITE_CYAN_R;
								layer2[c].fg[1] = SPRITE_CYAN_G;
								layer2[c].fg[2] = SPRITE_CYAN_B;
							}
							else
							{
								// lighten fg if not transparent
								layer2[c].fg[0] = std::min(255, layer2[c].fg[0] + SPRITE_LIGHTEN_AMOUNT);
								layer2[c].fg[1] = std::min(255, layer2[c].fg[1] + SPRITE_LIGHTEN_AMOUNT);
								layer2[c].fg[2] = std::min(255, layer2[c].fg[2] + SPRITE_LIGHTEN_AMOUNT);
							}

							if (bk_transp)
							{
								// set bk to swoosh color
								layer2[c].bk[0] = SPRITE_CYAN_R;
								layer2[c].bk[1] = SPRITE_CYAN_G;
								layer2[c].bk[2] = SPRITE_CYAN_B;
							}
							else
							{
								// lighten bk if not transparent
								layer2[c].bk[0] = std::min(255, layer2[c].bk[0] + SPRITE_LIGHTEN_AMOUNT);
								layer2[c].bk[1] = std::min(255, layer2[c].bk[1] + SPRITE_LIGHTEN_AMOUNT);
								layer2[c].bk[2] = std::min(255, layer2[c].bk[2] + SPRITE_LIGHTEN_AMOUNT);
							}
							// - keep underlying glyph
						}
				}
			}
			else
			if (merge[c].bk[0] != SPRITE_MAGENTA_R || merge[c].bk[1] != SPRITE_MAGENTA_G || merge[c].bk[2] != SPRITE_MAGENTA_B)
				layer2[c] = merge[c];
		}
	}

	// [DATA-CONTRACT:SPRITE] Atlas layout parsing — angles, animations, projections from Layer 0
	// WHY: Layer 0 encodes the sprite atlas layout via digit glyphs in specific cells.
	//   - layer0[0].glyph (top-left cell, column 0): Number of view angles.
	//     If > 0, the sprite has multiple viewing directions and projs=2 (proj + reflection).
	//     If 0 or non-digit, treat as single-angle (angles=1, projs=1).
	//   - layer0[height*a].glyph (first cell of column a, a=1..): Animation frame count.
	//     Each non-zero digit starts a new animation with that many frames.
	//     Scanning stops at first non-digit or zero.
	//   - layer0[1] (row 1, col 0): Y projection reference offset (in half-blocks)
	//   - layer0[1+height] (row 1, col 1): Y reflection reference offset
	//   - layer0[2] (row 2, col 0): Z projection offset (negated)
	//   - layer0[2+height] (row 2, col 1): Z reflection offset (negated)
	//
	// Grid layout: columns = projs * sum(anim_lengths), rows = angles
	// Frame size: fr_width = width / total_columns, fr_height = height / angles
	// TODO(PIPELINE-FIX): max_anims is hard-coded to 16. If an .xp file encodes more
	// than 16 animations, anim_len[] will overflow (buffer overrun). No bounds checking.
	// TODO(PIPELINE-FIX): If width is not evenly divisible by fr_num_x (or height by
	// fr_num_y), the last column/row of frames will have incorrect dimensions.
	// No divisibility validation is performed.

	const int max_anims = 16;
	int projs = 1;
	int anims = 1;
	int anim_len[max_anims] = { 1 };
	int anim_sum = 1;
	int angles = layer0[0].GetDigit();
	if (angles > 0)
	{
		projs = 2;
		anim_sum = 0;
		anims = 0;
		for (int a = 1; a < width; a++)
		{
			int len = layer0[height*a].GetDigit();
			if (len > 0)
			{
				anim_sum += len;
				anim_len[anims] = len;
				anims++;
			}
			else
				break;
		}

		if (!anims)
		{
			anims = 1;
			anim_sum = 1;
		}
	}
	else
	{
		angles = 1;

		anim_sum = 0;
		anims = 0;
		for (int a = 1; a < width; a++)
		{
			int len = layer0[height*a].GetDigit();
			if (len > 0)
			{
				anim_sum += len;
				anim_len[anims] = len;
				anims++;
			}
			else
				break;
		}

		if (!anims)
		{
			anims = 1;
			anim_sum = 1;
		}
	}

	// [DATA-CONTRACT:SPRITE] Atlas assembly — grid of frames from .xp pixel data
	// WHY: The .xp file's pixel grid is subdivided into individual sprite frames.
	// fr_num_x columns (projections * total animation frames) and fr_num_y rows (angles).
	// Each sub-rectangle becomes one Sprite::Frame with its own cell buffer.
	int fr_num_x = (projs * anim_sum);
	int fr_num_y = angles;

	// [VALIDATION] Frame alignment check - VAL-04
	// WHY: If dimensions don't divide evenly, frame extraction will be misaligned,
	// causing visual artifacts or memory access issues.
	if (fr_num_x > 0 && width % fr_num_x != 0)
	{
		if (!quiet_failures)
			fprintf(stderr, "[SPRITE] %s: width %d not divisible by frame count %d (projs=%d anims=%d anim_sum=%d)\n",
					path, width, fr_num_x, projs, anims, anim_sum);
		free(out);
		return 0;
	}
	if (fr_num_y > 0 && height % fr_num_y != 0)
	{
		if (!quiet_failures)
			fprintf(stderr, "[SPRITE] %s: height %d not divisible by angles %d\n",
					path, height, fr_num_y);
		free(out);
		return 0;
	}

	int frames = fr_num_y * fr_num_x;
	Sprite::Frame* atlas = (Sprite::Frame*)malloc(sizeof(Sprite::Frame)*frames);
	if (!atlas)
	{
		if (!quiet_failures)
			fprintf(stderr, "[SPRITE] %s: failed to allocate sprite atlas (%d frames)\n", path, frames);
		free(out);
		return 0;
	}
	memset(atlas, 0, sizeof(Sprite::Frame)*frames);

	int fr_width = width / fr_num_x;
	int fr_height = height / fr_num_y;

	int ref[2][3] =
	{
		{ fr_width,0,0 },
		{ fr_width,2*fr_height,0 },
	};

	if (height >= 2)
	{
		int y_proj = layer0[1+0].GetDigit();
		int y_refl = layer0[1+height].GetDigit();
		if (y_proj >= 0 && y_proj <= 2 * fr_height)
			ref[0][1] = y_proj;
		if (y_refl >= 0 && y_refl <= 2 * fr_height)
			ref[1][1] = 2 * fr_height - y_refl;
	}

	if (height >= 3)
	{
		int z_proj = layer0[2 + 0].GetDigit();
		int z_refl = layer0[2 + height].GetDigit();
		if (z_proj >= 0)
			ref[0][2] = -z_proj;
		if (z_refl >= 0)
			ref[1][2] = -z_refl;
	}

	for (int fr_y = 0; fr_y < fr_num_y; fr_y++)
	{
		for (int fr_x = 0; fr_x < fr_num_x; fr_x++)
		{
			Sprite::Frame* frame = atlas + fr_x + fr_y * fr_num_x;
			// alloc frame store
			// ...
			frame->width = fr_width;
			frame->height = fr_height;

			frame->meta_xy[0] = 0;
			frame->meta_xy[1] = 0;

			AnsiCell* c = (AnsiCell*)malloc(sizeof(AnsiCell)*fr_width*fr_height);
			if (!c)
			{
				if (!quiet_failures)
					fprintf(stderr, "[SPRITE] %s: failed to allocate frame cell store (%dx%d)\n", path, fr_width, fr_height);
				FreeSpriteFrameStores(atlas, frames);
				free(atlas);
				free(out);
				return 0;
			}
			frame->cell = c;

			// FL-4131 Phase 1: attach inert GlyphPlane when sidecar present.
			// cells=NULL; Phase 2 will populate. CP437-only sprites get NULL.
			if (has_sidecar_for_frames)
			{
				frame->glyph_plane = glyph_plane_alloc(fr_width, fr_height);
				if (!frame->glyph_plane)
				{
					if (!quiet_failures)
						fprintf(stderr, "[SPRITE] %s: failed to allocate glyph sidecar plane (%dx%d)\n", path, fr_width, fr_height);
					FreeSpriteFrameStores(atlas, frames);
					free(atlas);
					free(out);
					return 0;
				}
			}
			else
				frame->glyph_plane = NULL;

			frame->ref[0] = fr_width; // in half blocks! (means x-middle)

			int rgb_div;

			if (projs<2 || 2 * fr_x < fr_num_x)
			{
				// proj:

				// PLAYER
				// frame->ref[1] = 2; // in half blocks!
				// frame->ref[2] = -1; // foot cell (spare=1) gets z = 0.5*dz/dy (half cell above reference)

				// WOLFIE
				//frame->ref[1] = 3;
				//frame->ref[2] = -2;

				frame->ref[0] = ref[0][0];
				frame->ref[1] = ref[0][1];
				frame->ref[2] = ref[0][2];
				rgb_div = 255;

			}
			else
			{
				// refl

				// PLAYER
				// frame->ref[1] = 2 * fr_height - 2; // in half blocks!
				// frame->ref[2] = -15; // foot cell (spare=7) gets z = -0.5*dz/dy (half cell below reference)

				// WOLFIE
				//frame->ref[1] = 2*fr_height -1;
				//frame->ref[2] = -17;

				frame->ref[0] = ref[1][0];
				frame->ref[1] = ref[1][1];
				frame->ref[2] = ref[1][2];
				rgb_div = 400;
			}

			int x0 = fr_x * fr_width, x1 = x0 + fr_width;
			int y0 = fr_y * fr_height, y1 = y0 + fr_height;
			for (int y = y1 - 1; y >= y0; y--)
			{
				for (int x = x0; x < x1; x++, c++)
				{
					int cell = x * height + y;
					XPCell* c0 = layer0 + cell;
					XPCell* c1 = layer1 + cell;
					XPCell* c2 = visual_layer + cell;

					if (c0->glyph == 2) // meta-pos
					{
						// in half cells
						frame->meta_xy[0] = (x-x0)*2 - frame->ref[0];
						frame->meta_xy[1] = (y1 - 1 - y)*2 - frame->ref[1];
					}

					// FL-4131 Phase 2: AnsiCell.gl stays CP437 fallback bytes only.
					// Extended glyphs (>255) store 0x3F '?' as visible fallback;
					// the real GlyphId travels in GlyphPlane.cells.
					if (c2->glyph > 255)
						c->gl = 0x3F;
					else
						c->gl = (uint8_t)c2->glyph;

					// Populate GlyphPlane with the real GlyphId (CP437 or extended).
					if (frame->glyph_plane)
					{
						int cell_index = (int)(c - frame->cell);
						frame->glyph_plane->cells[cell_index] = c2->glyph;
					}

					// [DATA-CONTRACT:SPRITE] Transparency detection — color key from Layer 0 bk
					// WHY: Transparency is determined by comparing Layer 2 colors against
					// Layer 0's background color (the "color key"). If a cell's fg or bk
					// matches layer0's bk exactly, that component is transparent.
					// Additionally, magenta bk (255,0,255) is REXPaint's native transparency
					// and forces both fg and bk transparent.
					// Swoosh is indicated by cyan (0,255,255) in fg or bk.
					// Special palette values after quantization: SPRITE_TRANSPARENT_INDEX = transparent, SPRITE_SWOOSH_INDEX = swoosh
					bool bk_transp = (c2->bk[0] == c0->bk[0] && c2->bk[1] == c0->bk[1] && c2->bk[2] == c0->bk[2]);
					bool fg_transp = (c2->fg[0] == c0->bk[0] && c2->fg[1] == c0->bk[1] && c2->fg[2] == c0->bk[2]);

					bool fg_swoosh = (c2->fg[0] == SPRITE_CYAN_R && c2->fg[1] == SPRITE_CYAN_G && c2->fg[2] == SPRITE_CYAN_B);
					bool bk_swoosh = (c2->bk[0] == SPRITE_CYAN_R && c2->bk[1] == SPRITE_CYAN_G && c2->bk[2] == SPRITE_CYAN_B);

					if (c2->bk[0] == SPRITE_MAGENTA_R && c2->bk[1] == SPRITE_MAGENTA_G && c2->bk[2] == SPRITE_MAGENTA_B)
					{
						// rexpaint transp
						bk_transp = true;
						fg_transp = true;
					}

					// [DATA-CONTRACT:SPRITE] Height encoding from Layer 1
					// WHY: Layer 1's glyph encodes the cell's height value (stored in spare).
					// '0'-'9' = heights 0-9, 'A'-'Z' = heights 10-35.
					// SPRITE_HEIGHT_UNDEFINED = undefined height (glyph was not a valid height digit).
					// The spare field is used by the physics/rendering pipeline to determine
					// the Z-height of each sprite cell for depth sorting and collision.
					if (c1->glyph >= '0' && c1->glyph <= '9')
						c->spare = c1->glyph - '0';
					else
						if (c1->glyph >= 'A' && c1->glyph <= 'Z')
							c->spare = 10 + c1->glyph - 'A';
						else
							c->spare = SPRITE_HEIGHT_UNDEFINED;

					if (bk_swoosh)
						c->bk = SPRITE_SWOOSH_INDEX;
					else
					if (bk_transp)
						c->bk = SPRITE_TRANSPARENT_INDEX;
					else
					{
						if (recolor)
						{
							for (int i = 0; i < recolor[0]; i++)
							{
								int j = 1 + 6 * i;
								const uint8_t* re_src = recolor + j;
								const uint8_t* re_dst = re_src + 3;

								if (c2->bk[0] == re_src[0] &&
									c2->bk[1] == re_src[1] &&
									c2->bk[2] == re_src[2])
								{
									c2->bk[0] = re_dst[0];
									c2->bk[1] = re_dst[1];
									c2->bk[2] = re_dst[2];
									break;
								}
							}
						}

						// Inline palette quantization (differs from RGB2PAL):
						// Formula: level = (component * 5 + 128) / rgb_div
						// - rgb_div=255 (projection): equivalent to (c*5+128)/255, maps 0-255 to 0-5
						//   with center-biased rounding (128 = 255/2)
						// - rgb_div=400 (reflection): produces darker result (fewer high levels)
						//   because the divisor is larger, compressing the range
						// Then applies same 16 + 36*r + 6*g + b palette index formula.
						int r = (c2->bk[0] * 5 + 128) / rgb_div;
						int g = (c2->bk[1] * 5 + 128) / rgb_div;
						int b = (c2->bk[2] * 5 + 128) / rgb_div;

						c->bk = 16 + 36 * r + g * 6 + b;
					}

					if (fg_swoosh)
						c->fg = SPRITE_SWOOSH_INDEX;
					else
					if (fg_transp)
						c->fg = SPRITE_TRANSPARENT_INDEX;
					else
					{
						if (recolor)
						{
							for (int i = 0; i < recolor[0]; i++)
							{
								int j = 1 + 6 * i;
								const uint8_t* re_src = recolor + j;
								const uint8_t* re_dst = re_src + 3;

								if (c2->fg[0] == re_src[0] &&
									c2->fg[1] == re_src[1] &&
									c2->fg[2] == re_src[2])
								{
									c2->fg[0] = re_dst[0];
									c2->fg[1] = re_dst[1];
									c2->fg[2] = re_dst[2];
									break;
								}
							}
						}

						int r = (c2->fg[0] * 5 + 128) / rgb_div;
						int g = (c2->fg[1] * 5 + 128) / rgb_div;
						int b = (c2->fg[2] * 5 + 128) / rgb_div;

						c->fg = 16 + 36 * r + g * 6 + b;
					}

					if (recolor)
					{
						for (int i = 1 + 6 * recolor[0]; recolor[i]; i += 2)
						{
							if (c2->glyph == recolor[i])
							{
								c->gl = recolor[i + 1];
								break;
							}
						}
					}
				}
			}
		}
	}

	Sprite* sprite = (Sprite*)malloc(sizeof(Sprite) + sizeof(Sprite::Anim));

	sprite->refs = 1;
	sprite->recolored = recolor != 0; // so editor can skip'em
	sprite->cookie = 0;
	sprite->projs = projs;
	sprite->angles = angles;
	sprite->anims = anims;
	sprite->atlas = atlas;
	sprite->frames = frames;

	for (int i = 0; i < anims; i++)
		sprite->anim[i].length = anim_len[i];

	for (int anim = 0; anim < sprite->anims; anim++)
		sprite->anim[anim].frame_idx = (int*)malloc(sizeof(int) * 2/*proj,refl*/ * sprite->angles * sprite->anim[anim].length);

	for (int refl = 0; refl < 2; refl++)
	{
		int rx = refl * fr_num_x / 2;
		for (int angl = 0; angl < sprite->angles; angl++)
		{
			int x = rx;
			int y = angl;
			for (int anim = 0; anim < sprite->anims; anim++)
			{
				for (int frame = 0; frame < sprite->anim[anim].length; frame++)
				{
					int idx = x + y * fr_num_x;
					sprite->anim[anim].frame_idx[(refl*sprite->angles + angl)*sprite->anim[anim].length + frame] = idx;
					x++;
				}
			}
		}
	}

	float cos30 = (float)cos(30 * (M_PI / 180));
	float z = fr_height / cos30 * HEIGHT_SCALE;
	float dz = ref[0][1]*0.5f / cos30 * HEIGHT_SCALE;

	float zoom = 2.0f / 3.0f;

	sprite->proj_bbox[0] = -fr_width * .5f * zoom;
	sprite->proj_bbox[1] = +fr_width * .5f * zoom;
	sprite->proj_bbox[2] = -fr_width * .5f * zoom;
	sprite->proj_bbox[3] = +fr_width * .5f * zoom;
	sprite->proj_bbox[4] = -dz * zoom;
	sprite->proj_bbox[5] = (z-dz) * zoom;

	/////////////////////////////////
	
	// u_inflate_free(out);
	free(out);

	if (detached)
	{
		sprite->prev = 0;
		sprite->next = 0;
	}
	else
	{
		sprite->prev = sprite_tail;
		if (sprite_tail)
			sprite_tail->next = sprite;
		else
			sprite_head = sprite;
		sprite->next = 0;
		sprite_tail = sprite;
	}

	if (name)
		sprite->name = strdup(name);
	else
		sprite->name = 0;

	sprite->glyph_manifest = manifest;
	return sprite;
}

// WHY: Fills an axis-aligned rectangle in the AnsiCell buffer with a uniform cell value.
// Clips against the buffer boundaries. Used for clearing regions or painting solid backgrounds.
void FillRect(AnsiCell* ptr, int width, int height, int x, int y, int w, int h, AnsiCell ac)
{
	if (x < 0)
	{
		w -= -x;
		x = 0;
	}
	if (x + w > width)
		w = width - x;

	if (y < 0)
	{
		h -= -y;
		y = 0;
	}
	if (y + h > height)
		h = height - y;

	int x1 = x;
	int x2 = x + w;

	int y1 = y;
	int y2 = y + h;

	AnsiCell* dst = ptr + x1 + y1 * width;
	for (y = y1; y < y2; y++)
	{
		for (int i = 0; i < w; i++)
			dst[i] = ac;
		dst += width;
	}
}

// WHY: 4x4 ordered dither matrix (Bayer-like). Values 1-8 distribute thresholds
// spatially so that dithering produces an even pattern. When sprite_dither is set
// to N, cells where matrix[y%4][x%4] <= N are skipped, creating N/8 transparency.
static const int sprite_dither_matrix[4][4]=
{
	{1,5,3,5},
	{6,4,7,4},
	{3,5,2,5},
	{8,4,6,4}
};

static int sprite_dither = 0;

void SetSpriteDither(int eighths)
{
	sprite_dither = eighths;
}

// WHY: Dithered sprite blit — same as BlitSprite but skips cells based on a 4x4
// ordered dither matrix. The dither level (sprite_dither, 1-8) controls how many
// cells are skipped per 4x4 tile, producing a fade/transparency effect for sprites
// at distance or during transitions. Matrix values 1-8 are compared against the
// dither level; cells where matrix[y%4][x%4] <= dither are skipped.
void DitherSprite(AnsiCell* ptr, int width, int height, const Sprite::Frame* sf, int x, int y, const int clip[4], bool src_clip, AnsiCell* bk)
{
	const int dither = sprite_dither;

	int sx = 0, sy = 0, w = sf->width, h = sf->height;
	if (clip)
	{
		if (src_clip)
		{
			if (clip[0] >= clip[2] || clip[0] >= sf->width || clip[2] < 0 ||
				clip[1] >= clip[3] || clip[1] >= sf->height || clip[3] < 0)
				return;

			if (clip[0] < 0)
				x += -clip[0];
			else
			{
				sx = clip[0];
				w -= clip[0];
			}

			if (clip[2] < sx + w)
				w -= sx + w - clip[2];

			if (clip[1] < 0)
				y += -clip[1];
			else
			{
				sy = clip[1];
				h -= clip[1];
			}

			if (clip[3] < sy + h)
				h -= sy + h - clip[3];
		}
		else
		{
			if (x < clip[0])
			{
				w -= clip[0] - x;
				sx += clip[0] - x;
				x = clip[0];
			}
			if (x + w > clip[2])
				w = clip[2] - x;

			if (y < clip[1])
			{
				h -= clip[1] - y;
				sy += clip[1] - y;
				y = clip[1];
			}
			if (y + h > clip[3])
				h = clip[3] - y;
		}
	}

	if (x < 0)
	{
		w -= -x;
		sx += -x;
		x = 0;
	}
	if (x + w > width)
		w = width - x;

	if (y < 0)
	{
		h -= -y;
		sy += -y;
		y = 0;
	}
	if (y + h > height)
		h = height - y;

	int x1 = x;
	int x2 = x + w;

	int y1 = y;
	int y2 = y + h;

	if (x2 <= x1)
		return;

	AnsiCell* dst = ptr + x1 + y1 * width;
	const AnsiCell* src = sf->cell + sx + sy * sf->width;

	if (bk)
	{
		AnsiCell* ptr = dst;
		for (y = y1; y < y2; y++)
		{
			const int* my = sprite_dither_matrix[y&3];
			for (int i = 0; i < w; i++)
			{
				if (my[(x1+i)&3] <= dither)
					continue;
				ptr[i] = *bk;
			}
			ptr += width;
		}
	}

	for (y = y1; y < y2; y++)
	{
		const int* my = sprite_dither_matrix[y&3];
		for (int i = 0; i < w; i++)
		{
			if (my[(x1+i)&3] <= dither)
				continue;

			if (src[i].bk == SPRITE_TRANSPARENT_INDEX)
			{
				// if both bk and fg are transparent -> ignore
				// if bk is transparent and gl is <space> -> ignore
				if (src[i].fg == SPRITE_TRANSPARENT_INDEX || src[i].gl == 32)
					continue;
				
				switch (src[i].gl)
				{
					case 220: // fg-lower
						dst[i].bk = AverageGlyph(dst + i, 0xC);
						break;

					case 221: // fg-left
						dst[i].bk = AverageGlyph(dst + i, 0xA);
						break;

					case 222: // fg-right
						dst[i].bk = AverageGlyph(dst + i, 0x5);
						break;

					case 223: // fg-upper
						dst[i].bk = AverageGlyph(dst + i, 0x3);
						break;

					default:
						dst[i].bk = AverageGlyph(dst + i, 0xF);
				}

				dst[i].fg = src[i].fg;
				// FL-4131 (M1 guard): copies a uint8 CP437 glyph between two
				// AnsiCell grids. Extended GlyphIds travel in the parallel
				// GlyphPlane sidecar (engine/glyph_plane.h, Phase 2). When
				// extended-glyph sprite composition ships, propagate
				// src_plane->cells[i] -> dst_plane->cells[i] alongside this.
				dst[i].gl = src[i].gl;
			}
			else
			{
				if (src[i].fg == SPRITE_TRANSPARENT_INDEX)
				{
					// if fg is transparent and gl is <full-blk> -> ignore
					if (src[i].gl == 219)
						continue;

					switch (src[i].gl)
					{
						case 220: // fg-lower
							dst[i].fg = AverageGlyph(dst + i, 0x3);
							break;

						case 221: // fg-left
							dst[i].fg = AverageGlyph(dst + i, 0x5);
							break;

						case 222: // fg-right
							dst[i].fg = AverageGlyph(dst + i, 0xA);
							break;

						case 223: // fg-upper
							dst[i].fg = AverageGlyph(dst + i, 0xC);
							break;

						default:
							dst[i].fg = AverageGlyph(dst + i, 0xF);
					}

					dst[i].bk = src[i].bk;
					// FL-4131 (M1 guard): CP437 byte copy; extended GlyphIds
					// travel in the GlyphPlane sidecar, not in AnsiCell.gl.
					dst[i].gl = src[i].gl;
				}
				else // if none of fg and bk is transparent -> replace
					dst[i] = src[i];
			}
		}
		dst += width;
		src += sf->width;
	}
}

void BlitSprite(AnsiCell* ptr, int width, int height, const Sprite::Frame* sf, int x, int y, const int clip[4], bool src_clip, AnsiCell* bk)
{
	// [FLOW:RENDER]
	// Software rasterizer for ANSI cells.
	// Blits a sprite frame onto the target buffer (ptr) with clipping and transparency handling.
	// - ptr: Target buffer (screen/texture)
	// - sf: Source sprite frame
	// - clip: Clipping rectangle [x1, y1, x2, y2]
	// - src_clip: If true, clip is applied to source coordinates; otherwise, to destination.
	// Handles "swoosh" transparency where specific glyphs/colors indicate transparency.

	if (sprite_dither>0)
	{
		if (sprite_dither>8)
			return;
		DitherSprite(ptr, width, height, sf, x, y, clip, src_clip, bk);
		return;
	}
	
	int sx = 0, sy = 0, w = sf->width, h = sf->height;
	if (clip)
	{
		if (src_clip)
		{
			if (clip[0] >= clip[2] || clip[0] >= sf->width || clip[2] < 0 ||
				clip[1] >= clip[3] || clip[1] >= sf->height || clip[3] < 0)
				return;

			if (clip[0] < 0)
				x += -clip[0];
			else
			{
				sx = clip[0];
				w -= clip[0];
			}

			if (clip[2] < sx + w)
				w -= sx + w - clip[2];

			if (clip[1] < 0)
				y += -clip[1];
			else
			{
				sy = clip[1];
				h -= clip[1];
			}

			if (clip[3] < sy + h)
				h -= sy + h - clip[3];
		}
		else
		{
			if (x < clip[0])
			{
				w -= clip[0] - x;
				sx += clip[0] - x;
				x = clip[0];
			}
			if (x + w > clip[2])
				w = clip[2] - x;

			if (y < clip[1])
			{
				h -= clip[1] - y;
				sy += clip[1] - y;
				y = clip[1];
			}
			if (y + h > clip[3])
				h = clip[3] - y;
		}
	}

	if (x < 0)
	{
		w -= -x;
		sx += -x;
		x = 0;
	}
	if (x + w > width)
		w = width - x;

	if (y < 0)
	{
		h -= -y;
		sy += -y;
		y = 0;
	}
	if (y + h > height)
		h = height - y;

	int x1 = x;
	int x2 = x + w;

	int y1 = y;
	int y2 = y + h;

	if (x2 <= x1)
		return;

	AnsiCell* dst = ptr + x1 + y1 * width;
	const AnsiCell* src = sf->cell + sx + sy * sf->width;

	if (bk)
	{
		AnsiCell* ptr = dst;
		for (y = y1; y < y2; y++)
		{
			for (int i = 0; i < w; i++)
				ptr[i] = *bk;
			ptr += width;
		}
	}

	for (y = y1; y < y2; y++)
	{
		for (int i = 0; i < w; i++)
		{
			if (src[i].bk == SPRITE_TRANSPARENT_INDEX)
			{
				// if both bk and fg are transparent -> ignore
				// if bk is transparent and gl is <space> -> ignore
				if (src[i].fg == SPRITE_TRANSPARENT_INDEX || src[i].gl == 32)
					continue;
				
				switch (src[i].gl)
				{
					case 220: // fg-lower
						dst[i].bk = AverageGlyph(dst + i, 0xC);
						break;

					case 221: // fg-left
						dst[i].bk = AverageGlyph(dst + i, 0xA);
						break;

					case 222: // fg-right
						dst[i].bk = AverageGlyph(dst + i, 0x5);
						break;

					case 223: // fg-upper
						dst[i].bk = AverageGlyph(dst + i, 0x3);
						break;

					default:
						dst[i].bk = AverageGlyph(dst + i, 0xF);
				}

				dst[i].fg = src[i].fg;
				// FL-4131 (M1 guard): copies a uint8 CP437 glyph between two
				// AnsiCell grids. Extended GlyphIds travel in the parallel
				// GlyphPlane sidecar (engine/glyph_plane.h, Phase 2). When
				// extended-glyph sprite composition ships, propagate
				// src_plane->cells[i] -> dst_plane->cells[i] alongside this.
				dst[i].gl = src[i].gl;
			}
			else
			{
				if (src[i].fg == SPRITE_TRANSPARENT_INDEX)
				{
					// if fg is transparent and gl is <full-blk> -> ignore
					if (src[i].gl == 219)
						continue;

					switch (src[i].gl)
					{
						case 220: // fg-lower
							dst[i].fg = AverageGlyph(dst + i, 0x3);
							break;

						case 221: // fg-left
							dst[i].fg = AverageGlyph(dst + i, 0x5);
							break;

						case 222: // fg-right
							dst[i].fg = AverageGlyph(dst + i, 0xA);
							break;

						case 223: // fg-upper
							dst[i].fg = AverageGlyph(dst + i, 0xC);
							break;

						default:
							dst[i].fg = AverageGlyph(dst + i, 0xF);
					}

					dst[i].bk = src[i].bk;
					// FL-4131 (M1 guard): CP437 byte copy; extended GlyphIds
					// travel in the GlyphPlane sidecar, not in AnsiCell.gl.
					dst[i].gl = src[i].gl;
				}
				else // if none of fg and bk is transparent -> replace
					dst[i] = src[i];
			}
		}
		dst += width;
		src += sf->width;
	}
}

// WHY: Draws a box-drawing frame (UI border) using CP437 box-drawing glyphs.
// Uses a bit-encoded glyph lookup table (bit2gl[16]) where each bit represents
// a connection direction (bottom-left, bottom-right, top-left, top-right).
// The 'combine' flag enables merging with existing box-drawing characters in the
// buffer, so overlapping frames share junction glyphs.
void PaintFrame(AnsiCell* ptr, int width, int height, int x, int y, int w, int h, const int dst_clip[4], uint8_t fg, uint8_t bk, bool dbl, bool combine)
{
	int sx = x, sy = y;
	int sw = w, sh = h;

	bool l = true, r = true, b = true, t = true;

	x++; y++;
	w -= 2; h -= 2;

	if (dst_clip)
	{
		if (sx < dst_clip[0])
			l = false;
		if (sx+sw > dst_clip[2])
			r = false;
		if (sy < dst_clip[1])
			b = false;
		if (sy + sh > dst_clip[3])
			t = false;

		if (x < dst_clip[0])
		{
			w -= dst_clip[0] - x;
			x = dst_clip[0];
		}

		if (x + w > dst_clip[2])
			w = dst_clip[2] - x;

		if (y < dst_clip[1])
		{
			h -= dst_clip[1] - y;
			y = dst_clip[1];
		}

		if (y + h > dst_clip[3])
			h = dst_clip[3] - y;
	}

	if (sx < 0)
		l = false;
	if (sx + sw > width)
		r = false;
	if (sy < 0)
		b = false;
	if (sy + sh > height)
		t = false;


	if (x < 0)
	{
		w -= -x;
		x = 0;
	}
	if (x + w > width)
		w = width - x;

	if (y < 0)
	{
		h -= -y;
		y = 0;
	}
	if (y + h > height)
		h = height - y;

	if (w <= 0 || h <= 0)
		return;

	int x1 = x, x2 = x + w;
	int y1 = y*width, y2 = (y + h)*width;

	static const uint8_t bit2gl[16] = { 0, 0, 0, 187, 0, 205, 201, 203, 0, 188, 186, 185, 200, 202, 204, 206 };

	static uint8_t gl2bit_raw[256] = { 0x0 };
	static uint8_t gl2bit_cmb[256] = { 0xFF };
	if (gl2bit_cmb[0] == 0xff)
	{
		memset(gl2bit_cmb, 0, 176);
		for (int i = 176; i < 224; i++)
		{
			for (int j = 0; j < 16; j++)
				if (i == bit2gl[j])
					gl2bit_cmb[i] = j;
		}
	}

	uint8_t* gl2bit = combine ? gl2bit_cmb : gl2bit_raw;

	if (b)
	{
		AnsiCell* row = ptr + sy * width;

		if (l)
		{
			row[sx].gl = bit2gl[gl2bit[row[sx].gl] | 0xC];
			if (fg != SPRITE_TRANSPARENT_INDEX)
				row[sx].fg = fg;
			if (bk != SPRITE_TRANSPARENT_INDEX)
				row[sx].bk = bk;
		}

		for (int dx = x1; dx < x2; dx++)
		{
			row[dx].gl = bit2gl[gl2bit[row[dx].gl] | 0x5];
			if (fg != SPRITE_TRANSPARENT_INDEX)
				row[dx].fg = fg;
			if (bk != SPRITE_TRANSPARENT_INDEX)
				row[dx].bk = bk;
		}

		if (r)
		{
			row[sx + sw-1].gl = bit2gl[gl2bit[row[sx + sw-1].gl] | 0x9];
			if (fg != SPRITE_TRANSPARENT_INDEX)
				row[sx + sw-1].fg = fg;
			if (bk != SPRITE_TRANSPARENT_INDEX)
				row[sx + sw-1].bk = bk;
		}
	}

	if (t)
	{
		AnsiCell* row = ptr + (sy+sh-1) * width;

		if (l)
		{
			row[sx].gl = bit2gl[gl2bit[row[sx].gl] | 0x6];
			if (fg != SPRITE_TRANSPARENT_INDEX)
				row[sx].fg = fg;
			if (bk != SPRITE_TRANSPARENT_INDEX)
				row[sx].bk = bk;
		}

		for (int dx = x1; dx < x2; dx++)
		{
			row[dx].gl = bit2gl[gl2bit[row[dx].gl] | 0x5];
			if (fg != SPRITE_TRANSPARENT_INDEX)
				row[dx].fg = fg;
			if (bk != SPRITE_TRANSPARENT_INDEX)
				row[dx].bk = bk;
		}

		if (r)
		{
			row[sx + sw-1].gl = bit2gl[gl2bit[row[sx + sw-1].gl] | 0x3];
			if (fg != SPRITE_TRANSPARENT_INDEX)
				row[sx + sw-1].fg = fg;
			if (bk != SPRITE_TRANSPARENT_INDEX)
				row[sx + sw-1].bk = bk;
		}
	}

	if (l)
	{
		AnsiCell* col = ptr + sx;
		for (int dy = y1; dy < y2; dy+=width)
		{
			col[dy].gl = bit2gl[gl2bit[col[dy].gl] | 0xA];
			if (fg != SPRITE_TRANSPARENT_INDEX)
				col[dy].fg = fg;
			if (bk != SPRITE_TRANSPARENT_INDEX)
				col[dy].bk = bk;
		}
	}

	if (r)
	{
		AnsiCell* col = ptr + sx + sw - 1;
		for (int dy = y1; dy < y2; dy+=width)
		{
			col[dy].gl = bit2gl[gl2bit[col[dy].gl] | 0xA];
			if (fg != SPRITE_TRANSPARENT_INDEX)
				col[dy].fg = fg;
			if (bk != SPRITE_TRANSPARENT_INDEX)
				col[dy].bk = bk;
		}
	}
}


// FL-4131: glyph_coverage[256] relocated to engine/glyph_coverage_lookup.cpp
// behind a GlyphId-typed lookup API (engine/glyph_coverage_lookup.h).
// AverageGlyph / AverageGlyphTransp now route through glyph_coverage_lookup()
// so extended GlyphIds fail closed at the compositor rather than silently
// indexing the byte table.

// WHY: Maps each palette index (0-255) to a packed RGB value where each nibble
// is a color level (0-5). Indices 0-15 and 232-255 map to 0x000 (unused/undefined).
// Indices 16-231 are the 216-color cube: for index i, the packed value is
// 0x(b)(g)(r) where r,g,b are levels 0-5. Used by DarkenGlyph to decompose a
// palette color into per-component levels for darkening arithmetic.
static const uint16_t palette_rgb[256] =
{
	0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000,
	0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000,

	0x000, 0x001, 0x002, 0x003, 0x004, 0x005,
	0x010, 0x011, 0x012, 0x013, 0x014, 0x015,
	0x020, 0x021, 0x022, 0x023, 0x024, 0x025,
	0x030, 0x031, 0x032, 0x033, 0x034, 0x035,
	0x040, 0x041, 0x042, 0x043, 0x044, 0x045,
	0x050, 0x051, 0x052, 0x053, 0x054, 0x055,

	0x100, 0x101, 0x102, 0x103, 0x104, 0x105,
	0x110, 0x111, 0x112, 0x113, 0x114, 0x115,
	0x120, 0x121, 0x122, 0x123, 0x124, 0x125,
	0x130, 0x131, 0x132, 0x133, 0x134, 0x135,
	0x140, 0x141, 0x142, 0x143, 0x144, 0x145,
	0x150, 0x151, 0x152, 0x153, 0x154, 0x155,

	0x200, 0x201, 0x202, 0x203, 0x204, 0x205,
	0x210, 0x211, 0x212, 0x213, 0x214, 0x215,
	0x220, 0x221, 0x222, 0x223, 0x224, 0x225,
	0x230, 0x231, 0x232, 0x233, 0x234, 0x235,
	0x240, 0x241, 0x242, 0x243, 0x244, 0x245,
	0x250, 0x251, 0x252, 0x253, 0x254, 0x255,

	0x300, 0x301, 0x302, 0x303, 0x304, 0x305,
	0x310, 0x311, 0x312, 0x313, 0x314, 0x315,
	0x320, 0x321, 0x322, 0x323, 0x324, 0x325,
	0x330, 0x331, 0x332, 0x333, 0x334, 0x335,
	0x340, 0x341, 0x342, 0x343, 0x344, 0x345,
	0x350, 0x351, 0x352, 0x353, 0x354, 0x355,

	0x400, 0x401, 0x402, 0x403, 0x404, 0x405,
	0x410, 0x411, 0x412, 0x413, 0x414, 0x415,
	0x420, 0x421, 0x422, 0x423, 0x424, 0x425,
	0x430, 0x431, 0x432, 0x433, 0x434, 0x435,
	0x440, 0x441, 0x442, 0x443, 0x444, 0x445,
	0x450, 0x451, 0x452, 0x453, 0x454, 0x455,

	0x500, 0x501, 0x502, 0x503, 0x504, 0x505,
	0x510, 0x511, 0x512, 0x513, 0x514, 0x515,
	0x520, 0x521, 0x522, 0x523, 0x524, 0x525,
	0x530, 0x531, 0x532, 0x533, 0x534, 0x535,
	0x540, 0x541, 0x542, 0x543, 0x544, 0x545,
	0x550, 0x551, 0x552, 0x553, 0x554, 0x555,

	0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000,
	0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000,
	0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000, 0x000
};

// WHY: Bumps each RGB component of a palette color up by 1 level (out of 6),
// clamping at 5. Used by swoosh merging to create a brightened highlight effect
// on cells beneath swoosh overlays. Decomposes 16+36*r+6*g+b back to components,
// increments each, then recomposes.
int LightenColor(int c)
{
	// todo make lookup table
	// !!!
	c -= 16;
	int r = c / 36;
	c -= 36 * r;
	int g = c / 6;
	c -= 6 * g;
	int b = c;

	r += 1;
	g += 1;
	b += 1;

	if (r > 5)
		r = 5;
	if (g > 5)
		g = 5;
	if (b > 5)
		b = 5;

	return 16 + r * 36 + g * 6 + b;
}

// WHY: Darkens a cell's average color by 2 levels per component. Used for shadow/depth
// effects. First averages the cell's visual color via AverageGlyph, then decomposes
// the palette index into RGB components via palette_rgb[] lookup and subtracts 2.
int DarkenGlyph(const AnsiCell* ptr)
{
	int rgb = palette_rgb[AverageGlyph(ptr,0xF)];
	int r = rgb & 0xF;
	int g = (rgb>>4) & 0xF;
	int b = (rgb>>8) & 0xF;
	r = r > 1 ? r - 2 : 0;
	g = g > 1 ? g - 2 : 0;
	b = b > 1 ? b - 2 : 0;

	return 16 + r + 6 * g + 36 * b;
}

// WHY: Determines the dominant color of a cell within a masked region (quadrants).
// Each CP437 glyph has a precomputed 4-quadrant coverage from
// engine/glyph_coverage_lookup.cpp:
//   bits 0-3: bottom-left, bits 4-7: bottom-right, bits 8-11: top-left, bits 12-15: top-right
// The mask selects which quadrants to consider (bit 1=BL, 2=BR, 4=TL, 8=TR).
// If the glyph's coverage exceeds 50% in the masked area, returns fg; otherwise bk.
// Falls back to the other color if the primary is transparent (255).
// Used by BlitSprite/DitherSprite for compositing half-transparent cells.
//
// FL-4131: thin AnsiCell wrapper. The moved compositing core lives in
// engine/glyph_compositor.cpp (AverageGlyphForId). The byte-domain ptr->gl is
// uint8_t, so this wrapper can only flow LEGACY_CP437 today; the GlyphId
// entry point preserves the EXTENDED_UNBOUND / SENTINEL fail-closed branch
// for future callers that pass an effective GlyphId directly.
int AverageGlyph(const AnsiCell* ptr, int mask)
{
	return AverageGlyphForId((GlyphId)ptr->gl, ptr->fg, ptr->bk, mask);
}

// WHY: Same coverage-based averaging as AverageGlyph but does NOT fall back to
// the other color when the result is transparent (255). Returns the raw fg or bk
// even if transparent. Used during swoosh merging where transparency needs to be
// preserved rather than substituted.
//
// FL-4131: thin AnsiCell wrapper around AverageGlyphTranspForId
// (engine/glyph_compositor.cpp).
int AverageGlyphTransp(const AnsiCell* ptr, int mask)
{
	return AverageGlyphTranspForId((GlyphId)ptr->gl, ptr->fg, ptr->bk, mask);
}
