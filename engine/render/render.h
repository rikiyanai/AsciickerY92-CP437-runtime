#pragma once

// =============================================================================
// Software Renderer Interface — Public API and Data Structures
// =============================================================================
// Defines data structures for the custom ASCII software rasterizer.
//
// Core Concepts:
// - AnsiCell: Represents a single character cell on the terminal (FG, BG, Glyph).
// - MatCell:  One entry in a Material's shade table (RGB fg/bg + glyph + blend).
// - Material: A 4x16 table of MatCells mapping (elevation, shade) to appearance.
// - Renderer: Opaque handle to the render state (SampleBuffer, sprite list, etc).
//
// WHY this header exists: render.cpp is 4400+ lines and defines the Renderer
// struct internally. This header exposes only the public interface — the data
// types that flow between subsystems (AnsiCell for terminal output, Material
// for terrain appearance) and the Render()/Project/Unproject API.
//
// Rendering Pipeline (6 stages, see render.cpp for full documentation):
//   1. Clear -> 2. Terrain -> 3. World -> 4. Shadow -> 5. Reflection -> 6. Resolve
//   Final output: width*height grid of AnsiCells in 256-color xterm palette.
//
// EDUCATIONAL DATA FLOW (MatCell -> final terminal output):
//   1. MatCell is a "style swatch" for one material at one slope/elevation
//      category and one lighting level. It stores full RGB fg/bg colors plus a
//      CP437 glyph. This is still high-level material appearance data, not yet
//      a screen pixel or terminal cell.
//   2. Material is a 4x16 table of MatCells. The renderer chooses one entry
//      with two runtime indices:
//      - elevation category (0-3), derived from nearby terrain elevation bits
//      - shade index (0-15), derived from diffuse lighting
//   3. Terrain and mesh rasterizers do not write AnsiCells directly. They write
//      into a hidden 2x supersampled SampleBuffer made of Sample structs.
//   4. Each Sample stores the current winning depth, one packed visual value,
//      one diffuse value, and per-sample flags:
//      - terrain: Sample.visual is a terrain visual word
//      - mesh:    Sample.visual is direct RGB555 color when spare&0x8 is set
//   5. Four Samples (a 2x2 block) are later resolved into one final AnsiCell.
//      During that resolve pass, the renderer may:
//      - look up a MatCell from Material::shade[elevation][shade]
//      - average RGB from the four sub-samples
//      - choose a half-block glyph for sharp edges
//      - quantize final RGB into xterm palette indices
//   6. AnsiCell is the final terminal-facing format:
//      - fg = xterm palette index for foreground color
//      - bk = xterm palette index for background color
//      - gl = CP437 glyph code to draw in that cell
//      - spare = final-cell bookkeeping/debug field
//
// A useful beginner mental model:
//   MatCell  = material appearance recipe
//   Material = table of recipes for slope x light combinations
//   Sample   = hidden sub-pixel work buffer entry
//   AnsiCell = final visible terminal character cell
// =============================================================================

#include <stdint.h>
#include "material_glyph_plane.h"
#include <assert.h>
#include <algorithm> // std::min/max

#include "terrain.h"
#include "world.h"

// FL-4131 Phase 3 — native renderer shader pin
// ─────────────────────────────────────────────────────────────────────────────
// MODEL_PIN: shader_lookup_lut_model_pinned
//
// PURPOSE: Pin the renderer-side model for resolving extended GlyphIds at the
// AnsiCell→pixel boundary. The byte-domain AnsiCell.gl below stays uint8 CP437
// (Law: no widening). Extended glyph identity travels alongside the AnsiCell
// grid in the GlyphPlane sidecar attached by sprite.cpp (Phase 2 admission).
//
// ATLAS-OF-ATLASES BINDING MODEL:
//   - A glyph manifest (engine/glyph_manifest.h, Phase 2) is loaded once per
//     sprite/material and verified by SHA-256 over RFC8785 canonical JSON.
//   - Each manifest declares a set of repertoire pages. A page is a 16x16
//     glyph atlas texture (same layout as the existing CP437 font[active_font]
//     atlas, e.g. font.tex). Pages are not concatenated into one Unicode mega-
//     atlas; they are bound as an "atlas of atlases" so WebGL1 page binding
//     rules are preserved.
//   - Page binding key: (manifest.content_pack_id, page_index). Renderer
//     looks pages up by manifest pointer + page_index, never by raw glyph id.
//
// SHADER LUT MODEL (GlyphId → atlas coords):
//   - The lookup is a sorted-array binary search inside GlyphManifest.entries
//     (cpu side) or a 1D R32UI lookup texture indexed by a small admission-
//     compacted index (gpu side). Either form returns the tuple
//     (page_index, atlas_x_cell, atlas_y_cell) ∈ [0,15] x [0,15] within page.
//   - Renderer composes: page_atlas[page_index].sample(
//         (atlas_xy + fract_cell) / 16.0  ).a
//     mirroring the existing CP437 path `font.sample((cp437_xy + fract) / 16)`
//     in editor/asciiid.cpp around the term_fs glyph shader.
//   - CP437 ids 0..255 keep their existing in-font code path. They are NEVER
//     re-resolved through the extended LUT; the extended LUT covers >=256
//     only. This preserves the legacy_assets_no_diff gate.
//
// FAIL-CLOSED FALLBACK CONTRACT (Phase 3 owner of unknown_glyph_fallback):
//   - If a sample reads a sidecar GlyphPlane cell with an extended id that the
//     bound manifest has NO admission entry for, the renderer must substitute
//     `manifest.fallback_glyph_id` (always an admitted extended glyph >255;
//     enforced by Phase 2 parse-time validator: entries[].glyph_id > 255 and
//     fallback_glyph_id ∈ entries) and emit a one-shot [FL-4131] diagnostic.
//     It must not crash, must not sample undefined atlas memory, and must not
//     silently truncate to glyph 0 / glyph '?' without going through the
//     manifest fallback. The fallback re-attempts the extended LUT (succeeds
//     because the validator guarantees fallback ∈ entries).
//   - GLYPH_ID_NONE / GLYPH_ID_UNRESOLVED sentinels also route through the
//     manifest fallback. They are never sampled directly.
//
// LANE / OWNERSHIP:
//   - Native GL renderer (render.cpp) and ASCIIID editor shader (asciiid.cpp,
//     `asciiid_shader_manifest_lookup` anchor) are the two Phase 3 consumers.
//   - Web buffer is Phase 5 (own widening decision for the cell shader path).
//   - Multiplayer manifest hash carriage is Phase 7.
//   - This pin is the single architectural owner for the LUT model. Renaming,
//     moving, or duplicating this marker is an FL-4131 contract regression.
//
// COMPANION ANCHORS (review M5 — structured block, grep-stable):
//   editor/asciiid.cpp (term_fs_src)      : MODEL_PIN asciiid_shader_manifest_lookup
//   editor/asciiid.cpp (picker grid)      : MODEL_PIN asciiid_input_model_pinned
//   web/game_web.cpp                      : MODEL_PIN web_extended_glyph_buffer
//   web/game_web.html (fragment shader)   : MODEL_PIN web_extended_glyph_buffer
//   web/game_web.html (reject decoder)    : MODEL_PIN multiplayer_manifest_hash_match
//   server/protocol/protocol_join.h       : MODEL_PIN multiplayer_manifest_hash_match
//   engine/glyph_manifest.h               : Phase 2 manifest + RFC8785+SHA-256
//   engine/glyph_plane.h                  : Phase 2 sprite-side carrier
//   engine/material_glyph_plane.h         : Phase 2 material-side carrier
// ─────────────────────────────────────────────────────────────────────────────

// WHY AnsiCell: This is the final output format — one per terminal character
// cell. The renderer's 6-stage pipeline ultimately produces a width*height
// grid of these. The 4-byte layout matches the terminal's color model:
// fg/bk are 256-color xterm palette indices (0-15: system, 16-231: 6x6x6
// RGB cube, 232-255: grayscale ramp). gl is a CP437 glyph code because
// the game renders into a virtual text terminal, not a pixel framebuffer.
struct AnsiCell
{
	// Target format for the terminal buffer.
	// R, G, B, A ordering matches standard color structs, but usage here is:
	uint8_t fg;    // WHY uint8_t: 256-color xterm palette index for foreground
	uint8_t bk;    // WHY uint8_t: 256-color xterm palette index for background
	uint8_t gl;    // WHY CP437: game renders to virtual text terminal, not pixels
	uint8_t spare; // WHY spare: reserved for per-cell flags (e.g. 0xFF = rendered)
};

// WHY MatCell: Materials need full RGB888 precision for blending — the 256-color
// xterm palette is only applied at the final resolve stage. MatCell stores
// separate fg[3]/bg[3] RGB triplets because the terrain shader blends multiple
// material samples together in linear RGB space before quantizing to palette.
// The glyph and blend flags control how this cell composites with what's
// already in the buffer (transparency, multiply, screen blend modes).
struct MatCell
{
	// This is still "material space" color, not a final terminal palette entry.
	uint8_t fg[3];	// WHY RGB888: full precision for material blending before palette quantize
	uint8_t gl;		// glyph code (CP437) — the character pattern for this shade level
	uint8_t bg[3];	// WHY separate bg: bg and fg can be different colors for dithered appearance
	uint8_t flags;

	// WHY flags bit layout: 8 bits pack both transparency and blend mode info
	// so each MatCell is exactly 8 bytes (cache-friendly for shade table access).
	//
	// transparency mask :
	// 0x1 - fg
	// 0x2 - gl
	// 0x4 - bg
	//
	// blend modes 3x3 bits:
	// 0x03 2-bits fg blend mode (0:replace, 1:multiply, 2:screen, 3:transparent)
	// 0x04 glyph write mask (0:replace, 1:keep)
	// 0x18 2-bits bg blend mode (0:replace, 1:multiply, 2:screen, 3:transparent)
	// 3 bits left!

};

// WHY Material shade[4][16]: The terrain renderer computes two indices per
// sample — an elevation category (4 levels: lowered, high, raised, low) and
// a shade level (16 steps from dark to bright, derived from diffuse lighting
// 0-255 divided by 17). shade[elevation][shade] gives the MatCell to use.
// This 2D lookup replaces runtime per-pixel lighting math with a single
// table fetch, which is critical for CPU rasterizer performance.
struct Material
{
	// Read as: "for this terrain slope/elevation case and this light level,
	// use this MatCell recipe".
	MatCell shade[4][16]; // WHY [4]: 4 elevation levels (terrain slope categories)
	                      // WHY [16]: 16 shade steps (diffuse/17, 0xFF->15)
	int mode; // WHY mode: material flags (animation speed, special blend behavior)
	// FL-4131 Phase 2: extended glyph carrier for material shade table.
	// NULL for legacy CP437-only materials. Populated when material sidecar
	// + manifest load + hash verify + admission pass. Freed in material unload.
	MaterialGlyphPlane* glyph_plane;
};

// WHY opaque struct: Renderer holds large internal state (SampleBuffer,
// sprite sort lists, Perlin noise generator, view matrices) that should not
// leak into every translation unit that includes render.h. The Create/Delete
// pattern keeps the full definition in render.cpp and exposes only a pointer.
struct Renderer;
Renderer* CreateRenderer(uint64_t stamp);
void DeleteRenderer(Renderer* r);

// return null-terminated array of item pointers that are reachable by player
/*
Item** Render(Renderer* r, uint64_t stamp, Terrain* t, World* w, float water, 		// scene
			float zoom, float yaw, const float pos[3], const float lt[4],	// view
			int width, int height, AnsiCell* ptr,  // target
			Sprite* sprite, int anim, int frame, float dir, 	// player
			const int scene_shift[2]); // special fx
*/

// WHY these parameter groups: Render() needs everything to produce one frame.
//   scene:  world state (terrain, world objects, water level)
//   view:   camera config (zoom, yaw, position, lighting direction+ambient)
//   target: output buffer (terminal dimensions + AnsiCell pointer)
//   player: the player instance (hidden during render, re-shown after)
//   fx:     scene_shift for screen-shake effects, perspective toggle
struct RenderFrameInput;
struct RenderFrameReport;
void Render(Renderer* r, uint64_t stamp, Terrain* t, World* w, float water, 		// scene
	float zoom, float yaw, const float pos[3], const float lt[4],	// view
	int width, int height, AnsiCell* ptr,  // target
	Inst* player, // player
	const int scene_shift[2],
	bool perspective,
	const RenderFrameInput* frame_input = nullptr,
	RenderFrameReport* out_report = nullptr);

// WHY both Project and Unproject: gameplay needs both directions —
// Project maps world->screen (for UI overlays on world objects),
// Unproject maps screen->world (for mouse/keyboard picking).
// WHY 2D vs 3D unproject: 2D reads depth from SampleBuffer automatically
// (click at screen XY, get world XYZ). 3D takes an explicit Z value when
// the caller already knows the depth plane (e.g. water surface picking).
bool ProjectCoords(Renderer* r, const float pos[3], int view[3]); // like a sprite!
bool UnprojectCoords2D(Renderer* r, const int xy[2], float pos[3]); // reads height from buffer first!
bool UnprojectCoords3D(Renderer* r, const int xy[3], float pos[3]); // reads height from buffer first!

// WHY render_break_point: debug aid — when set to a valid (x,y) cell
// coordinate, the resolve loop will hit a breakpoint at that cell for
// step-through debugging of the 2x2 downsample logic.
extern int render_break_point[2];
