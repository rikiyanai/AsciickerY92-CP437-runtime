// ============================================================================
// FONT1.H - CP437 Font Atlas Public API
// ============================================================================
//
// [DATA-CONTRACT:SPRITE] Font atlas loaded from sprites/font-1.xp
//
// PURPOSE:
// Public API for rendering ASCII text to AnsiCell buffers using a custom
// CP437-based font atlas. Provides three color skins for menu state
// visualization (normal, selected, disabled).
//
// ============================================================================
// SKIN CONSTANTS
// ============================================================================
//
// FONT1_GREY_SKIN (0):  Normal text (neutral grey tones)
//                       Used for: Unselected menu items, general text
//
// FONT1_GOLD_SKIN (1):  Selected/highlighted text (bright yellow/gold)
//                       Used for: Currently selected menu item, attention
//
// FONT1_PINK_SKIN (2):  Disabled/inactive text (magenta/pink)
//                       Used for: Unavailable menu items, locked content
//
// ============================================================================
// CHARACTER SET COVERAGE
// ============================================================================
//
// Supported: ASCII 32-127 (printable characters only)
//   - Uppercase: A-Z (26 characters)
//   - Digits: 0-9 (10 characters)
//   - Punctuation: Space, period (.), question (?), basic symbols
//   - Total: 52 glyphs
//
// NOT supported:
//   - Lowercase (a-z): Renders as invalid (skipped)
//   - Special symbols: Most Unicode characters unsupported
//   - Control characters: ASCII 0-31 (tab, newline handled separately)
//
// ============================================================================
// ATLAS LAYOUT SUMMARY
// ============================================================================
//
// Grid: 4 rows x 13 columns = 52 glyphs
// Cell size: 5x5 pixels per glyph
// Variable-width advance: 2-4 pixels (proportional spacing)
// Vertical advance: 4 pixels per line
//
// ============================================================================
// FUNCTIONS
// ============================================================================
//
// LoadFont1():      Load font atlas with three color skins from sprites/font-1.xp
// FreeFont1():      Unload font atlas and free memory (all three skins)
//
// Font1Size():      Measure string dimensions (width, height) for layout calculations
//                   Accumulates variable-width advances, counts newlines
//
// Font1Paint():     Render string to AnsiCell buffer with specified skin
//                   Supports newlines, optional underline decoration
//
// Font1UnderLine(): Render underline decoration below text
//                   Uses horizontal line glyph repeated across width
//
// ============================================================================

#ifndef FONT1_H
#define FONT1_H

void LoadFont1();
void FreeFont1();

#define FONT1_GREY_SKIN 0
#define FONT1_GOLD_SKIN 1
#define FONT1_PINK_SKIN 2

void Font1Size(const char* str, int* w, int* h);
void Font1Paint(AnsiCell* ptr, int width, int height, int x, int y, const char* str, int skin = 0, bool underline = false);
void Font1UnderLine(AnsiCell* ptr, int width, int height, int x, int y, int w, int skin = 0);

#endif
