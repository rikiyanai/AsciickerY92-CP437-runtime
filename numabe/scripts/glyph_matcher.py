"""
GlyphLibrary and GlyphMatcher for CP437 font processing.

This module provides functionality to:
1. Load CP437 glyphs from a font sheet
2. Find the best-matching glyph for a given 12x12 pixel chunk
"""

from PIL import Image


class GlyphLibrary:
    """Load and manage CP437 glyphs from font sheet."""

    def __init__(self, font_path="assets/fonts/cp437_12x12.png"):
        """Initialize glyph library by loading font sheet.

        Args:
            font_path: Path to CP437 font sheet (192x192 pixels, 16x16 grid of 12x12 glyphs)
        """
        self.font_path = font_path
        self.sheet = Image.open(font_path).convert("RGB")
        self.glyphs = []

        self._extract_glyphs()

    def _extract_glyphs(self):
        """Extract all 256 glyphs from the font sheet."""
        # Verify dimensions
        assert self.sheet.size == (192, 192), (
            f"Font sheet must be 192x192, got {self.sheet.size}"
        )

        glyph_size = 12

        # 16x16 grid
        for row in range(16):
            for col in range(16):
                x = col * glyph_size
                y = row * glyph_size
                glyph = self.sheet.crop((x, y, x + glyph_size, y + glyph_size))
                self.glyphs.append(glyph)

        # Verify exactly 256 glyphs loaded
        assert len(self.glyphs) == 256, (
            f"Expected 256 glyphs, loaded {len(self.glyphs)}"
        )

    def get_glyph(self, index):
        """Get glyph by CP437 index.

        Args:
            index: CP437 character code (0-255)

        Returns:
            PIL Image object representing the glyph (12x12 RGB)
        """
        if index < 0 or index >= 256:
            raise IndexError(f"Glyph index must be 0-255, got {index}")
        return self.glyphs[index]


class GlyphMatcher:
    """Find best-matching CP437 glyph for a given 12x12 pixel chunk."""

    def __init__(self, font_path="assets/fonts/cp437_12x12.png"):
        """Initialize glyph matcher.

        Args:
            font_path: Path to CP437 font sheet
        """
        self.library = GlyphLibrary(font_path)

    def find_best_match(self, chunk_12x12):
        """Find the glyph with minimum squared Euclidean distance to the chunk.

        Args:
            chunk_12x12: PIL Image of 12x12 pixels to match

        Returns:
            int: CP437 glyph index (0-255) of best-matching glyph
        """
        min_distance = float("inf")
        best_index = 0

        for idx, glyph in enumerate(self.library.glyphs):
            distance = self._glyph_distance(chunk_12x12, glyph)
            if distance < min_distance:
                min_distance = distance
                best_index = idx

        return best_index

    def _glyph_distance(self, chunk, glyph):
        """Calculate squared Euclidean distance between chunk and glyph.

        Args:
            chunk: 12x12 PIL Image (RGB)
            glyph: 12x12 PIL Image (RGB)

        Returns:
            int: Total squared distance (sum of RGB squared differences)
        """
        total_distance = 0

        for y in range(12):
            for x in range(12):
                r1, g1, b1 = chunk.getpixel((x, y))
                r2, g2, b2 = glyph.getpixel((x, y))

                # Squared Euclidean distance (no sqrt needed for ranking)
                total_distance += (r1 - r2) ** 2
                total_distance += (g1 - g2) ** 2
                total_distance += (b1 - b2) ** 2

        return total_distance
