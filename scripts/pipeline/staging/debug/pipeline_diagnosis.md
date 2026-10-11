# PNG→XP Pipeline Diagnosis

**Date:** 2026-02-04
**Phase:** 35-02 (Research & Diagnosis)
**Status:** Root cause identified

## Executive Summary

The PNG→XP pipeline produces all-black output because **processor_core.py returns color INDICES (0-15) instead of color RGB tuples**, but the assembler expects RGB tuples and writes the indices directly as RGB values. An index of 0 becomes RGB (0, 0, 0) = black.

**Root Cause Classification:**
- [X] processor_core color index extraction bug (returns indices, not RGB)
- [X] Assembler layer packing bug (writes indices as RGB without palette lookup)

## Data Flow Analysis

### Complete Pipeline Flow

```
PNG Input (RGB pixels)
    ↓
[STAGE 1: GENERATE] pipeline.py:778
    ↓ Image.open() → PIL Image (RGB mode)
    ↓
[STAGE 2: SLICE] pipeline.py:831
    ↓ ImageSlicer.slice() → List[PIL Image] (frames)
    ↓
[STAGE 3: PROCESS] pipeline.py:838-882
    ↓ For each frame:
    ↓   1. Save to temp PNG
    ↓   2. ImageProcessor.process_image(temp_path)
    ↓      ↓
    ↓      [processor_core.py:89] yields (grid_x, grid_y, glyph_idx, color_idx)
    ↓      ↓
    ↓      color_idx from _quantize_chunk():
    ↓        - Reads center pixel RGB
    ↓        - Calls quantize_rgb_to_index(r, g, b) → returns INDEX (0-15)
    ↓      ↓
    ↓      ❌ BUG: Returns color_idx (0-15), not RGB tuple
    ↓   3. Reconstruct grid_data[row][col] = (glyph_idx, color_idx, color_idx)
    ↓      ❌ BUG: pipeline.py:879 stores INDEX, not RGB
    ↓
[STAGE 4: ASSEMBLE] pipeline.py:900-921
    ↓ XPAssembler.assemble(processed_frames, metadata, output_path)
    ↓   assembler.py:211 writes cell = (glyph, fg, bg) to visual layer
    ↓   ❌ BUG: Writes (glyph_idx, 0, 0) because color_idx=0 for most cells
    ↓
XP Output (all cells have fg=0, bg=0 → black)
```

## Root Cause

### File: `scripts/processor_core.py`
**Lines:** 91-109
**Function:** `_quantize_chunk()`

**Problem:** Returns palette INDEX (0-15) instead of RGB color tuple.

```python
def _quantize_chunk(self, chunk):
    """Quantize a 12x12 chunk to find its dominant color index.

    ...

    Returns:
        int: ANSI color index (0-15)    # ← RETURNS INDEX
    """
    center_x = chunk.width // 2
    center_y = chunk.height // 2
    r, g, b = chunk.getpixel((center_x, center_y))

    return quantize_rgb_to_index(r, g, b)  # ← Returns 0-15
```

The function **explicitly returns an integer index**, not an RGB tuple. When the input image has orange pixels (255, 128, 64), `quantize_rgb_to_index()` correctly finds the closest ANSI palette color and returns its INDEX (likely 9 for bright red or 11 for bright yellow), but this is an integer, not a color.

### File: `scripts/pipeline/pipeline.py`
**Lines:** 874-879
**Function:** `run()` Stage 3 processing loop

**Problem:** Stores color indices directly without palette lookup.

```python
for grid_x, grid_y, glyph_idx, color_idx in results:
    # WHY: (glyph_idx, color_idx, color_idx) uses the same
    # color for both foreground and background because the
    # current processor does not distinguish fg/bg.  The
    # assembler's Layer 2 writer reads all three fields.
    grid_data[grid_y][grid_x] = (glyph_idx, color_idx, color_idx)
    #                                       ↑         ↑
    #                               These are INDICES (0-15), not RGB tuples!
```

The pipeline stores `color_idx` (an integer 0-15) in the grid_data structure, assuming the assembler will interpret it as RGB.

### File: `scripts/pipeline/assembler.py`
**Lines:** 209-211
**Function:** `assemble()` Layer 2 visual layer population

**Problem:** Writes indices as-is without palette lookup.

```python
for fy, row_data in enumerate(frame_grid):
    for fx, cell in enumerate(row_data):
        if cell:
            visual_layer.data[base_y + fy][base_x + fx] = cell
            #                                              ↑
            # cell = (glyph_idx, color_idx, color_idx) from pipeline
            # XP format expects (glyph_idx, (r,g,b), (r,g,b))
```

The assembler writes the cell tuple as-is. XP format requires RGB tuples like `(255, 128, 64)`, but receives integers like `0` or `9`, which get interpreted as RGB `(0, 0, 0)` = black.

## Evidence

### Test Output

Running `test_conversion_produces_colored_output`:
```
Stage 3/4: Processing...
   Processed 1 frames
Stage 4/4: Assembly...
   Saved to staging/xp/test_colored.xp
Loading staging/xp/test_colored.xp...
Loaded 4 layers.

AssertionError: All foreground colors are black - pipeline produced all-black output
```

### Why All Black?

For an orange input pixel (255, 128, 64):
1. `quantize_rgb_to_index(255, 128, 64)` → returns **9** (bright red index)
2. Pipeline stores `(glyph_idx, 9, 9)` in grid_data
3. Assembler writes `(glyph_idx, 9, 9)` to XP cell
4. XP loader reads `fg = 9` as RGB → **(0, 0, 9)** in little-endian or similar corruption
5. Visual result: essentially black or very dark

Even if XP format interpreted `9` correctly as an integer RGB value, it would be `(9, 9, 9)` = very dark gray, nearly black.

Most cells likely quantize to index **0** (pure black) for darker pixels, creating the observed all-black output.

## Frame Geometry Analysis

The frame geometry contract is **also violated** due to a separate bug:

**Contract:** `fr_num_x = projs * anim_sum`, `fr_num_y = angles`

**Current behavior:**
- Pipeline does not encode `projs` metadata
- Assembler defaults to `projs = 1` (implicit)
- For multi-angle sprites, this should be `projs = 2` (projection + reflection)

**Evidence from test:**
```python
test_frame_geometry_contract_in_converted       FAILED (wrong dimensions)
test_projection_reflection_pair_contract        PASSED (projs=2 correct)
```

Wait - one test PASSES for projs=2? Let me check metadata encoding...

**Correction:** The assembler only encodes `angles` and `anims` in Layer 0 metadata:
```python
# assembler.py:163-166
meta_layer.data[0][0] = (self._encode_digit(angles), (255, 255, 255), (0, 0, 0))
for i, count in enumerate(anims):
    if i + 1 < sheet_width:
        meta_layer.data[0][i + 1] = (self._encode_digit(count), (255, 255, 255), (0, 0, 0))
```

**Missing:** No `projs` field is written. Engine may assume `projs = 1` or derive it from other metadata.

## Comparison with png2xp.cpp Reference

The C++ reference implementation takes a completely different approach:

### png2xp.cpp Architecture
```cpp
// Uses 216-color palette (6x6x6 RGB cube), not 16-color ANSI
// Lines 200-300: Dithering with half-blocks and quarter-blocks
// Writes a SINGLE layer, not 4 layers
// Encodes glyphs as Unicode block characters for shading
```

**Key differences:**
1. **Palette:** 216-color cube vs. 16-color ANSI
2. **Dithering:** Floyd-Steinberg dithering vs. nearest-color
3. **Output:** Single layer vs. 4-layer structure
4. **Use case:** General PNG→XP converter vs. sprite sheet pipeline

The Python pipeline and png2xp.cpp solve different problems:
- **png2xp.cpp:** Convert any PNG to XP with maximum fidelity (REXPaint editing)
- **Python pipeline:** Convert sprite sheets to multi-layer XP (game engine sprites)

**Conclusion:** png2xp.cpp is **not a drop-in reference** for the Python pipeline. The architectures are fundamentally different.

## Fix Strategy

### Fix 1: Pipeline stores RGB tuples, not indices (RECOMMENDED)

**Change:** `scripts/pipeline/pipeline.py` lines 874-879

```python
# BEFORE (broken):
for grid_x, grid_y, glyph_idx, color_idx in results:
    grid_data[grid_y][grid_x] = (glyph_idx, color_idx, color_idx)

# AFTER (fixed):
from scripts.pipeline.quantizer import ANSI_COLORS  # Import palette

for grid_x, grid_y, glyph_idx, color_idx in results:
    fg_rgb = ANSI_COLORS[color_idx]  # Convert index to RGB
    bg_rgb = ANSI_COLORS[color_idx]  # Same color for fg/bg
    grid_data[grid_y][grid_x] = (glyph_idx, fg_rgb, bg_rgb)
```

**Impact:**
- Minimal change (3 lines)
- Preserves processor_core.py behavior (returns indices)
- Fixes root cause: assembler receives RGB tuples as expected

**Rationale:**
- processor_core.py API is correct - returning indices is efficient
- Quantizer module provides ANSI_COLORS lookup table
- Pipeline is the orchestrator - it should translate between stages

### Fix 2: Processor returns RGB tuples directly (ALTERNATIVE)

**Change:** `scripts/processor_core.py` lines 91-109

```python
# BEFORE:
def _quantize_chunk(self, chunk):
    # ...
    return quantize_rgb_to_index(r, g, b)  # Returns 0-15

# AFTER:
def _quantize_chunk(self, chunk):
    # ...
    idx = quantize_rgb_to_index(r, g, b)
    return ANSI_COLORS[idx]  # Returns (r, g, b) tuple
```

**Impact:**
- Changes processor_core.py internal behavior
- Pipeline.py line 879 would work as-is
- Less clear API (function named `_quantize_chunk` returns RGB, not index)

**Rationale:**
- Fixes bug at source
- But violates naming convention - "quantize" implies returning palette index
- Makes processor less reusable (what if caller wants index?)

### Fix 3: Assembler performs palette lookup (LEAST RECOMMENDED)

**Change:** `scripts/pipeline/assembler.py` lines 209-211

```python
# BEFORE:
if cell:
    visual_layer.data[base_y + fy][base_x + fx] = cell

# AFTER:
if cell:
    glyph, fg_idx, bg_idx = cell
    fg_rgb = ANSI_COLORS[fg_idx] if isinstance(fg_idx, int) else fg_idx
    bg_rgb = ANSI_COLORS[bg_idx] if isinstance(bg_idx, int) else bg_idx
    visual_layer.data[base_y + fy][base_x + fx] = (glyph, fg_rgb, bg_rgb)
```

**Impact:**
- Assembler becomes palette-aware (added responsibility)
- Requires runtime type checking (performance cost)
- Unclear contract - sometimes RGB, sometimes indices?

**Rationale:**
- Violates single-responsibility principle
- Assembler should be a dumb packer, not a color converter
- Hard to debug when cells have mixed types

### Recommended Fix: Fix 1

**Location:** `scripts/pipeline/pipeline.py` lines 874-879
**Reason:**
- Pipeline is the orchestrator - it should translate between stage contracts
- Minimal change (3 lines)
- Clear data flow: processor returns indices, pipeline converts to RGB, assembler writes RGB
- No API changes to processor_core or assembler

## Next Steps (Plan 35-03)

1. **Implement Fix 1** in pipeline.py
2. **Add import:** `from scripts.pipeline.quantizer import ANSI_COLORS`
3. **Run tests:** `pytest scripts/pipeline/tests/test_png_to_xp.py -v`
4. **Verify:** All 5 failing tests should turn GREEN
5. **Edge cases:**
   - Verify magenta transparency (index 13 → (255, 0, 255))
   - Verify varied colors (not all same index)
   - Check frame geometry (separate bug, may need additional fix)

## Why v4.1 Fixes Failed

Previous fixes (v4.1 Phase 33) likely:
- Modified processor_core without fixing the index→RGB conversion
- Added palette quantization flags without fixing the lookup
- Changed assembler defaults without addressing root cause
- Never ran automated tests to verify the fix

**v4.1 failure analysis:**
- No automated tests existed
- Manual testing missed all-black output (or assumed it was correct)
- False "complete" status without verification

**v5.0 TDD approach:**
- Tests FIRST (Plan 35-01) - established regression baseline
- Diagnosis SECOND (Plan 35-02) - found root cause
- Fix THIRD (Plan 35-03) - implement with automated verification
- Tests verify fix immediately

## Metadata/Projs Bug (Secondary Issue)

**Separate from color bug** but also needs fixing:

**Problem:** Assembler does not encode `projs` field in Layer 0 metadata

**Fix location:** `scripts/pipeline/assembler.py` lines 163-166

**Add after angles encoding:**
```python
meta_layer.data[0][0] = (self._encode_digit(angles), (255, 255, 255), (0, 0, 0))
# ADD THIS:
meta_layer.data[0][1] = (self._encode_digit(metadata.get("projs", 1)), (255, 255, 255), (0, 0, 0))
# SHIFT anims encoding by 1:
for i, count in enumerate(anims):
    if i + 2 < sheet_width:  # Changed from i+1 to i+2
        meta_layer.data[0][i + 2] = (self._encode_digit(count), (255, 255, 255), (0, 0, 0))
```

**Note:** This requires engine change to READ projs field. Check sprite.cpp contract first.

## Summary

**Root Cause:** processor_core returns palette **indices** (0-15), but assembler expects palette **RGB tuples** like (255, 128, 64). The indices get written as-is, producing near-black output.

**Fix:** Pipeline (orchestrator) should convert indices to RGB using `ANSI_COLORS[color_idx]` lookup.

**Files to modify:**
1. `scripts/pipeline/pipeline.py` (3 lines, critical fix)
2. `scripts/pipeline/assembler.py` (optional, for projs metadata)

**Test verification:**
- 5 failing tests should turn GREEN
- Baseline tests remain GREEN (10/10)
- Final status: 18/18 passing
