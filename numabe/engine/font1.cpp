// ============================================================================
// FONT1.CPP - CP437 Font Atlas System
// ============================================================================
//
// PURPOSE:
// Custom CP437-based font atlas for rendering ASCII text to the game's
// AnsiCell buffer. Provides pixel-perfect retro text rendering with three
// color skins (grey, gold, pink) for menu states.
//
// WHY CUSTOM FONT:
// - Pixel-perfect retro aesthetic: 5x5 pixel cells for authentic retro look
// - Portable: Font atlas embedded in assets/sprites/ directory (no external deps)
// - Recolorable: Three skins via palette swap (grey=normal, gold=selected,
//   pink=disabled) enable menu state visualization without separate fonts
// - Lightweight: 52 glyphs only (uppercase + digits + basic punctuation),
//   no Unicode or lowercase = smaller memory footprint
//
// ============================================================================
// CP437 FONT ATLAS FORMAT (assets/sprites/font-1.xp)
// ============================================================================
//
// VIRTUAL 16x16 LAYOUT (CP437 Standard):
// CP437 defines 256 glyphs arranged in a 16x16 grid. Character code N maps to:
//   - Column: N & 0xF (low 4 bits)
//   - Row: N >> 4 (high 4 bits)
//
// ACTUAL 4x13 LAYOUT (Subset Implementation):
// This implementation uses only 52 glyphs arranged in 4 rows x 13 columns:
//   - Row 0: A-M (indices 0-12)
//   - Row 1: N-Z, 0-4 (indices 13-25, 26-30)
//   - Row 2: 5-9, space, . (indices 31-35, 36, 37)
//   - Row 3: ?, punctuation (indices 38-43)
//
// Glyph Cell Size: 5x5 pixels (font1_cell_w, font1_cell_h)
// Atlas Texture Size: 65x20 pixels (13*5 x 4*5)
//
// ============================================================================
// CHARACTER MAPPING TABLE (font1_cmap[96])
// ============================================================================
//
// Maps ASCII codes [32, 127] to atlas indices [0, 51].
// Invalid characters map to font1_cmap_invd = 99 (skip rendering).
//
// WHY OFFSET 32: ASCII codes 0-31 are control characters (tab, newline, etc.),
// not printable glyphs. Text rendering starts at ASCII 32 (space).
//
// WHY 96 ENTRIES: Covers ASCII 32-127 (printable characters).
//
// CHARACTER SET COVERAGE:
//   - A-Z: ASCII 65-90 → atlas indices 0-25
//   - 0-9: ASCII 48-57 → atlas indices 26-35
//   - Space: ASCII 32 → atlas index 36
//   - Period: ASCII 46 → atlas index 37
//   - Question: ASCII 63 → atlas index 38
//   - Punctuation: ASCII 33-37, 40-45 → atlas indices 39-42
//   - NO LOWERCASE: Minimizes atlas size for retro aesthetic
//
// ============================================================================
// VARIABLE-WIDTH ADVANCE TABLE (font1_xadv[44])
// ============================================================================
//
// Each glyph has custom horizontal advance (2-4 pixels) for proportional
// spacing. Array indexed by atlas index (0-43), NOT ASCII code.
//
// WHY VARIABLE WIDTH: Proportional spacing looks better than monospace for
// retro pixel fonts. 'I' advances 2 pixels, 'W' advances 4 pixels.
//
// WHY 44 ENTRIES: Matches the count of mapped glyphs (A-Z, 0-9, punctuation).
// Unmapped characters (font1_cmap[ch] = 99) don't need advance entries.
//
// Vertical Advance: font1_yadv = 4 pixels (constant for all glyphs).
// WHY 4 PIXELS: Slightly less than cell height (5px) for compact line spacing.
//
// ============================================================================
// SKIN RECOLORING SYSTEM
// ============================================================================
//
// Three skins via palette swap:
//   - FONT1_GREY_SKIN (0): Normal text (grey tones)
//   - FONT1_GOLD_SKIN (1): Selected/highlighted (yellow/gold)
//   - FONT1_PINK_SKIN (2): Disabled/inactive (magenta/pink)
//
// LoadFont1() loads assets/sprites/font-1.xp THREE times with different recolor
// palettes. Recolor format: {count, old_r, old_g, old_b, new_r, new_g, new_b, ..., 0, 0}
//
// WHY THREE SKINS: Menu highlighting system needs visual state feedback:
//   - Grey: Unselected menu items (neutral)
//   - Gold: Currently selected menu item (attention)
//   - Pink: Disabled menu items (unavailable)
// Alternative would require three separate font atlases (3x memory cost).
//
// ============================================================================
// RENDERING PIPELINE
// ============================================================================
//
// 1. Font1Size(): [FLOW:FONT] Measure string dimensions
//    - Accumulates horizontal advance (font1_xadv[glyph]) for each character
//    - Counts newlines for vertical dimension (line_count * font1_yadv)
//    - Returns bounding box (width, height) for layout calculations
//
// 2. Font1Paint(): [FLOW:FONT] Render string to AnsiCell buffer
//    - For each character:
//      a. Map ASCII → atlas index via font1_cmap[]
//      b. Calculate glyph position in atlas (col, row)
//      c. Apply Y-axis inversion (up_row = font1_rows - 1 - row)
//      d. Call BlitSprite() to render glyph
//      e. Advance cursor by font1_xadv[glyph]
//    - Optional underline rendering via horizontal line glyph
//
// 3. Font1UnderLine(): Render underline decoration
//    - Uses last glyph in atlas (font1_cols-1, row 0) as horizontal line
//    - Repeats line glyph across string width
//
// ============================================================================
// Y-AXIS INVERSION
// ============================================================================
//
// WHY Y-INVERSION: Sprite atlas origin is BOTTOM-LEFT (OpenGL Y-up coordinate
// system), but text origin is TOP-LEFT (screen Y-down coordinate system).
//
// Calculation: up_row = font1_rows - 1 - row
//
// Coordinate System Diagram:
//   Sprite Atlas (Y-up):          Text Rendering (Y-down):
//   Row 3 (top)    <-- up_row=3   Y=0 (top) --> Row 0
//   Row 2                         Y=1       --> Row 1
//   Row 1                         Y=2       --> Row 2
//   Row 0 (bottom) <-- up_row=0   Y=3 (bottom) --> Row 3
//
// Without inversion, text would render upside-down.
//
// ============================================================================
// BLITSPRITE INTEGRATION
// ============================================================================
//
// Font rendering reuses the sprite rendering pipeline (sprite.cpp):
//   - BlitSprite() handles transparency (alpha channel)
//   - BlitSprite() handles clipping (viewport bounds)
//   - BlitSprite() writes to AnsiCell buffer (same as game sprites)
//
// This unifies text and sprite rendering = single code path, consistent
// performance, no separate text renderer needed.
//
// ============================================================================
// KEY DATA STRUCTURES
// ============================================================================
//
// font1_sprite[3]:    Three Sprite* instances (one per skin)
// font1_cmap[96]:     ASCII-to-atlas-index mapping table
// font1_xadv[44]:     Variable-width horizontal advance table (per glyph)
// font1_yadv:         Constant vertical advance (4 pixels)
// font1_cell_w/h:     Glyph cell size (5x5 pixels)
// font1_rows/cols:    Atlas layout (4 rows, 13 columns)
//
// ============================================================================
// KEY FUNCTIONS
// ============================================================================
//
// LoadFont1():        [DATA-CONTRACT:SPRITE] Load font atlas with 3 skins
// FreeFont1():        Unload font atlas (all 3 skins)
// Font1Size():        [FLOW:FONT] Measure string dimensions (width, height)
// Font1Paint():       [FLOW:FONT] Render string to AnsiCell buffer
// Font1UnderLine():   Render underline decoration
//
// ============================================================================
// INTEGRATION POINTS
// ============================================================================
//
// DEPENDENCIES:
//   - sprite.cpp: BlitSprite() renders glyphs to AnsiCell buffer
//   - render.h: AnsiCell buffer format (fg, bk, gl indices)
//
// USED BY:
//   - mainmenu.cpp: Menu text rendering (title, items, loading screen)
//   - game.cpp: HUD text rendering (debug overlays, item names)
//
// ============================================================================

#include "sprite.h"
#include "font1.h"

static Sprite* font1_sprite[3] = { 0,0,0 }; // grey, gold, plain
extern char base_path[1024];

static const int font1_skins = 3;
// WHY 4 rows x 13 columns: Atlas contains 52 glyphs (subset of CP437 256-glyph set)
// Covers uppercase A-Z (26), digits 0-9 (10), space, period, question, punctuation (16)
static const int font1_rows = 4;
static const int font1_cols = 13;
static const int font1_size = 44;
// WHY 5x5 pixels: Retro pixel art aesthetic (large enough to be readable at 1x scale,
// small enough for compact text without anti-aliasing artifacts)
static const int font1_cell_w = 5;
static const int font1_cell_h = 5;
static uint8_t font1_yadv = 4;
// WHY variable-width advance: Proportional spacing looks better than monospace for
// retro pixel fonts. Narrow characters (I, space) advance 2 pixels, wide characters
// (W, M) advance 4 pixels, most characters advance 3 pixels.
// WHY 44 entries: Matches count of mapped glyphs in font1_cmap[] (excludes unmapped
// characters with cmap[ch] = 99). Indexed by atlas index, NOT ASCII code.
static uint8_t font1_xadv[] =
{
	3,  3,  3,  3,  3,  3,  3,  3,  2,  3,  3,  3,  4,
	3,  3,  3,  3,  3,  3,  3,  3,  3,  4,  3,  3,  3,
	3,  3,  3,  3,  3,  3,  3,  3,  3,  4,  2,  3,  4,
	3,  3,  4,  4,  3,
};

static const int font1_cmap_size = 96; // size of cmap array
static const int font1_cmap_invd = 99; // invalid character index in cmap
// WHY character mapping: ASCII codes don't match atlas positions. CP437 standard
// defines 256 glyphs in 16x16 grid, but we only implement 52 glyphs (uppercase,
// digits, basic punctuation). This table maps ASCII [32, 127] to atlas indices [0, 51].
// WHY 99 for invalid: Sentinel value outside valid atlas range [0-43] signals
// "skip rendering" in Font1Paint(). Characters without glyphs (lowercase, symbols)
// map to 99 and are silently ignored (no crash, no placeholder glyph).
// WHY 96 entries: Covers ASCII 32-127 (printable characters). Offset by 32 because
// ASCII 0-31 are control characters (tab, newline, etc.) with no printable glyphs.
static uint8_t font1_cmap[] =
{
	99,39,40,41,42,43,99,99,99,99,99,99,99,99,99,99,
	99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,99,
	36,99,99,99,99,99,99,99,99,99,99,99,99,99,37,99,
	26,27,28,29,30,31,32,33,34,35,99,99,99,99,99,38,
	99, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9,10,11,12,13,14,
	15,16,17,18,19,20,21,22,23,24,25,99,99,99,99,99
};

void LoadFont1()
{
	char path[1024+20];
	const char* name = "font-1.xp";
	sprintf(path, "%sassets/sprites/%s", base_path, name);
	// [DATA-CONTRACT:SPRITE] Load font atlas from assets/sprites/font-1.xp
	// WHY three loads: One per skin (grey, gold, pink) for menu state visualization.
	// Alternative would require three separate .xp files (3x storage cost).
	font1_sprite[0] = LoadSprite(path, name);
	// WHY these specific RGB values:
	// Gold skin (selected): Dark grey (85,85,85) → bright yellow (255,255,85)
	//                       Mid grey (170,170,170) → gold (255,204,0)
	//                       White (255,255,255) → gold (255,204,0)
	// Creates attention-grabbing highlight for currently selected menu item.
	uint8_t recolor1[] = { 3, 85,85,85, 255,255,85, 170,170,170, 255,204,0, 255,255,255, 255,204,0, 0,0  };
	font1_sprite[1] = LoadSprite(path, name, recolor1);
	// Pink skin (disabled): Dark grey (85,85,85) → bright magenta (255,153,255)
	//                       Mid grey (170,170,170) → magenta (255,0,255)
	//                       White (255,255,255) → dark magenta (255,51,255)
	// Creates muted/unusual color to signal "unavailable" state.
	uint8_t recolor2[] = { 3, 85,85,85, 255,153,255, 170,170,170, 255,0,255, 255,255,255, 255,51,255, 0,0 };
	font1_sprite[2] = LoadSprite(path, name, recolor2);
}

void FreeFont1()
{
	FreeSprite(font1_sprite[0]);
	FreeSprite(font1_sprite[1]);
	FreeSprite(font1_sprite[2]);
}

// [FLOW:FONT] Measure string dimensions for layout calculations
// Returns bounding box (width, height) by accumulating variable-width advances
// and counting newlines. Used by mainmenu.cpp and game.cpp for text centering.
void Font1Size(const char* str, int* w, int* h)
{
	int width = 0;
	int y = font1_yadv;
	int x = 0;

	while (*str)
	{
		if (*str == '\n')
		{
			width = x > width ? x : width;
			y += font1_yadv;
			x = 0;
		}
		else
		if (*str >=0 && *str < font1_cmap_size)
		{
			uint8_t chr = font1_cmap[*str];
			if (chr != font1_cmap_invd)
				x += font1_xadv[chr];
		}

		str++;
	}

	if (w)
		*w = x > width ? x : width;
	if (h)
		*h = y;
}

void Font1UnderLine(AnsiCell* ptr, int width, int height, int dx, int dy, int w, int skin)
{
	int y = dy;
	int x = dx;

	if (skin < 0 || skin >= font1_skins)
		return;

	Sprite::Frame* sf = font1_sprite[skin]->atlas;

	int clip[] = 
	{
		(font1_cols-1)*font1_cell_w, 0,
		font1_cols*font1_cell_w, 1
	};

	int x2 = x + w + 1;
	while (x < x2)
	{
		if (x + font1_cell_w > x2)
			clip[2] = clip[0] + x2 - x;
		BlitSprite(ptr, width, height, sf, x, y-1, clip);
		x += font1_cell_w;
	}
}

// [FLOW:FONT] Render string to AnsiCell buffer via sprite pipeline
// For each character: map ASCII→atlas index, calculate glyph clip rect with Y-inversion,
// call BlitSprite() to render glyph, advance cursor by variable-width. Supports newlines
// and optional underline decoration.
void Font1Paint(AnsiCell* ptr, int width, int height, int dx, int dy, const char* str, int skin, bool underline)
{
	int y = dy;
	int x = dx;

	if (skin < 0 || skin >= font1_skins)
		return;

	Sprite::Frame* sf = font1_sprite[skin]->atlas;

	while (*str)
	{
		if (*str == '\n')
		{
			y -= font1_yadv;
			x = dx;
		}
		else
		if (*str >= 0 && *str < font1_cmap_size)
		{
			if (*str == '?')
			{
				int a = 0;
			}
			uint8_t chr = font1_cmap[*str];
			if (chr != font1_cmap_invd)
			{
				int col = chr % font1_cols;
				int row = chr / font1_cols;

				// WHY Y-inversion: Sprite atlas origin is BOTTOM-LEFT (OpenGL Y-up),
				// but text origin is TOP-LEFT (screen Y-down). Without inversion,
				// text would render upside-down.
				//
				// Coordinate mapping:
				//   Atlas Y-up:     Row 3 (top)    Row 2    Row 1    Row 0 (bottom)
				//   Text Y-down:    up_row=3       up_row=2 up_row=1 up_row=0
				//   Screen Y-down:  Y=0 (top)      Y=1      Y=2      Y=3 (bottom)
				int up_row = font1_rows - 1 - row;

				int clip[] = { col * font1_cell_w, up_row * font1_cell_h, (col+1) * font1_cell_w, (up_row+1) * font1_cell_h };

				// [FLOW:FONT] Render glyph to AnsiCell buffer via sprite pipeline
				// BlitSprite() handles transparency (glyph alpha channel) and clipping
				BlitSprite(ptr, width, height, sf, x, y, clip);

				int adv = font1_xadv[chr];

				if (underline)
				{
					clip[0] = (font1_cols-1)*font1_cell_w;
					clip[1] = 0;
					clip[2] = clip[0] + adv + 1;
					clip[3] = 1;

					BlitSprite(ptr, width, height, sf, x, y-1, clip);
				}

				x += adv;
			}
		}

		str++;
	}
}
