"""
ImageProcessor - Unified processing pipeline for RGB to CP437 glyph conversion.

This module combines ColorQuantizer and GlyphMatcher into a single processing
pipeline that accepts RGB images and outputs glyph indices with grid coordinates.
"""

from PIL import Image

try:
    from color_quantizer import quantize_rgb_to_index
    from glyph_matcher import GlyphMatcher
except ImportError:
    from scripts.color_quantizer import quantize_rgb_to_index
    from scripts.glyph_matcher import GlyphMatcher


class ImageProcessor:
    """Process RGB images into CP437 glyphs with color indices.

    This processor takes an RGB image, divides it into 12x12 pixel chunks,
    quantizes each chunk to the 16-color ANSI palette, and finds the best-
    matching CP437 glyph for each chunk.

    Example:
        >>> processor = ImageProcessor()
        >>> for grid_x, grid_y, glyph_idx, color_idx in processor.process_image("image.png"):
        ...     print(f"Grid ({grid_x},{grid_y}): glyph={glyph_idx}, color={color_idx}")

    Output format:
        - grid_x: X coordinate in the output grid (0 = leftmost column)
        - grid_y: Y coordinate in the output grid (0 = topmost row)
        - glyph_idx: CP437 glyph index (0-255)
        - color_idx: ANSI color index (0-15)
    """

    def __init__(self, font_path="assets/fonts/cp437_12x12.png"):
        """Initialize the image processor.

        Args:
            font_path: Path to CP437 font sheet (192x192 pixels, 16x16 grid of 12x12 glyphs)
        """
        self.font_path = font_path
        self.glyph_matcher = GlyphMatcher(font_path)
        self.chunk_size = 12

    def process_image(self, image_path_or_img):
        """Process an RGB image into glyph and color indices.

        The image is divided into 12x12 pixel chunks. Each chunk is quantized
        to the 16-color ANSI palette, then matched to the best-fitting CP437 glyph.

        Args:
            image_path_or_img: Either a file path (str) or a PIL Image object

        Yields:
            tuple: (grid_x, grid_y, glyph_idx, color_idx) for each 12x12 chunk

        Example:
            >>> processor = ImageProcessor()
            >>> white = Image.new('RGB', (12, 12), (255, 255, 255))
            >>> result = list(processor.process_image(white))
            >>> grid_x, grid_y, glyph, color = result[0]
            >>> assert grid_x == 0
            >>> assert grid_y == 0
        """
        # Load image if path provided
        if isinstance(image_path_or_img, str):
            img = Image.open(image_path_or_img).convert("RGB")
        else:
            img = image_path_or_img.convert("RGB")

        w, h = img.size
        chunk_size = self.chunk_size

        # Process image in 12x12 chunks
        for y in range(0, h, chunk_size):
            for x in range(0, w, chunk_size):
                # Crop the chunk
                chunk = img.crop((x, y, x + chunk_size, y + chunk_size))

                # Quantize the chunk to find dominant color
                color_idx = self._quantize_chunk(chunk)

                # Find best matching glyph
                glyph_idx = self.glyph_matcher.find_best_match(chunk)

                # Yield grid coordinates and indices
                yield (x // chunk_size, y // chunk_size, glyph_idx, color_idx)

    def _quantize_chunk(self, chunk):
        """Quantize a 12x12 chunk to find its dominant color index.

        Uses the center pixel (or average) to determine the representative color
        for the chunk.

        Args:
            chunk: PIL Image of 12x12 pixels (RGB)

        Returns:
            int: ANSI color index (0-15)
        """
        # Use center pixel for color representative
        # (simpler than averaging all pixels, and sufficient for most use cases)
        center_x = chunk.width // 2
        center_y = chunk.height // 2
        r, g, b = chunk.getpixel((center_x, center_y))

        return quantize_rgb_to_index(r, g, b)
