// =============================================================================
// Sprite Data Structures (sprite.h)
// =============================================================================
//
// WHY: Defines the in-memory representation of sprite assets consumed by the
// render pipeline. After sprite.cpp loads and parses .xp files, these structs
// hold the frame atlas that render.cpp's RenderSprite/BlitSprite use for
// screen drawing.
//
// KEY STRUCTURES:
//
// 1. Sprite (Top-level container):
//    - atlas[]: Frame array indexed as [frames][angles][2]
//      The *2 factor stores projection/reflection pairs side-by-side
//    - projs: Number of projection variants (1=single, 2=projection+reflection)
//    - anims: Number of animation sequences (0 for still sprites)
//    - frames: Number of frames in atlas (1 for still sprites)
//    - angles: Number of view angles per frame (e.g., 8 for 8-directional)
//    - proj_bbox[6]: Bounding box across all frames/angles for culling
//
// 2. Sprite::Frame (Single frame data):
//    - cell[]: AnsiCell grid (width × height) with glyph + fg/bk palette indices
//    - ref[3]: Reference point (origin) for positioning, in half-cell units
//      (x,y scaled by 2 to allow sub-cell precision)
//    - meta_xy[2]: Special attachment point (e.g., crossbow arrow tip position)
//    - cell[].spare: Encodes cell height relative to ref[2] for depth sorting
//
// 3. Sprite::Anim (Animation sequence):
//    - length: Number of frames in animation
//    - frame_idx[]: Maps animation step to atlas frame index, per angle
//      Size: [angles * 2] to support projection/reflection variants
//
// ATLAS MEMORY LAYOUT:
// Atlas is a flat array indexed as atlas[frame_idx * angles * 2 + angle_idx * 2 + proj]
// where proj=0 for projection, proj=1 for reflection. The projs field controls
// how many variants exist (projs=1 means no reflection, only atlas[...* 2 + 0] used).
//
// RELATIONSHIP TO OTHER MODULES:
// - sprite.cpp: Loads .xp files, decompresses gzip, parses layers, populates Sprite
// - render.cpp: Reads Frame.cell[] for BlitSprite screen drawing, uses proj_bbox
//   for frustum culling before rendering
// - game.cpp: Calls LoadSprite/LoadPlayer to request sprites, manages lifecycle
//
// DATA CONTRACT:
// Frame.cell[].spare encodes the cell's height relative to ref[2] (z-origin).
// This enables depth sorting: cells with higher spare values render in front of
// cells with lower values, creating correct occlusion for multi-height sprites.
//
// =============================================================================

#pragma once

#include "render.h"
#include "glyph_plane.h"
#include "glyph_manifest.h"

struct Sprite
{
	int refs;
	bool recolored;

	struct Frame
	{
		int width;
		int height;
		int ref[3]; // on image x,y,z (x,y are int x2 units to allow half block refs)
		int meta_xy[2]; // some special position, ie crossbow's arrow tip (in half cells)
		AnsiCell* cell; // cell[].spare encodes cell height relative to ref[2]
		// FL-4131 Phase 1: versioned GlyphId sidecar carrier (inert, cells=NULL).
		// NULL for CP437-only frames (no sidecar). Populated when sprite was loaded
		// with a valid .glyph_profile.json sidecar. Phase 2 will populate cells.
		GlyphPlane* glyph_plane;
	};

	// from all frames angles anims and projections
	float proj_bbox[6];

	int projs;
	int anims;  // must be 0 for 'still' Sprite
	int frames; // must be 1 for 'still' Sprite
	int angles;
	Frame* atlas; // [frames][angles][2] (x2 because of projection/reflection)

	struct Anim
	{
		int length;
		int* frame_idx; // [angles * 2]
	};

	Sprite* next;
	Sprite* prev;
	char* name;
	void* cookie;
	// FL-4131 Phase 2: manifest loaded for extended-glyph admission.
	// NULL for CP437-only or no-sidecar sprites. Freed in FreeSprite.
	GlyphManifest* glyph_manifest;

	Anim anim[1];
};

Sprite* LoadSprite(const char* path, const char* name, /*bool has_refl = true,*/ const uint8_t* recolor = 0, bool detached = false, bool quiet_failures = false);
Sprite* LoadSpriteLayer(const char* path, const char* name, int visual_layer_index, const uint8_t* recolor = 0, bool detached = false, bool quiet_failures = false, bool merge_extra_layers = false);
Sprite* GetFirstSprite(bool all=true);
Sprite* GetPrevSprite(Sprite* s, bool all=true);
Sprite* GetNextSprite(Sprite* s, bool all=true);
int GetSpriteName(Sprite* s, char* buf, int size);

void SetSpriteCookie(Sprite* s, void* cookie);
void* GetSpriteCookie(Sprite* s);

Sprite* LoadPlayer(const char* path);
Sprite* CloneSpriteDetached(const Sprite* src, const char* name, bool recolored = false);
Sprite* CloneSpriteDetachedTransparent(const Sprite* src, const char* name, bool recolored = false);
Sprite* CreateSpriteDeltaOverlayDetached(const Sprite* base, const Sprite* variant, const char* name);
bool SpriteAtlasLayoutMatches(const Sprite* base, const Sprite* overlay);
void FreeSprite(Sprite* spr);
// FL-2500 / FL-2497: constrain a sprite to the visible-cell silhouette of a
// reference sprite with matching layout. Used by mounted wrapper rear/front
// surfaces so they cannot render outside their approved parity silhouette.
bool MaskSpriteByVisibleCells(Sprite* dst, const Sprite* mask);
void RecolorSpriteInPlace(Sprite* sprite, const uint8_t palette_map_proj[256], const uint8_t palette_map_refl[256], const uint8_t glyph_map[256]);

void BlitSprite(AnsiCell* ptr, int width, int height, const Sprite::Frame* sf, int x, int y, const int clip[4]=0, bool src_clip=true, AnsiCell* bk=0);
void PaintFrame(AnsiCell* ptr, int width, int height, int x, int y, int w, int h, const int dst_clip[4] = 0, uint8_t fg=0, uint8_t bk=255, bool dbl=true, bool combine=true);
void FillRect(AnsiCell* ptr, int width, int height, int x, int y, int w, int h, AnsiCell ac);

int AverageGlyph(const AnsiCell* ptr, int mask);
int DarkenGlyph(const AnsiCell* ptr);

int LightenColor(int c);
void SetSpriteDither(int eighths);
