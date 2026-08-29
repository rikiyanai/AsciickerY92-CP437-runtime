"""
Generate AI-style sprite sheet test fixture with magenta transparency.

This creates a 4×2 grid (8 frames total) for testing transparency handling:
- 2 animations × 4 frames each
- Each frame: 12×12 pixel cells, 8×4 grid = 96×48px
- Background: magenta (255, 0, 255)
- Character: Simple shapes (squares) for verification
"""

from PIL import Image, ImageDraw
import os

# Configuration
CELL_WIDTH = 12  # pixels per character
CELL_HEIGHT = 12  # pixels per character
GRID_W = 8  # character width in cells
GRID_H = 4  # character height in cells
FRAMES_PER_ANIM = 4
NUM_ANIMS = 2

# Colors
TRANSPARENT = (255, 0, 255)  # Magenta
CHAR_COLOR = (200, 200, 200)  # Light gray
CHAR_OUTLINE = (100, 100, 100)  # Dark gray


def create_test_character(draw, base_x, base_y, anim_idx, frame_idx):
    """Draw a simple animating character shape.

    Args:
        draw: ImageDraw object
        base_x, base_y: Top-left position in pixels
        anim_idx: Animation index (0 for idle, 1 for walk)
        frame_idx: Frame within animation (0-3)
    """
    # Calculate pulsing effect
    offset = (frame_idx % 2) * 2
    if anim_idx == 1:  # Walking bobbing
        offset = ((frame_idx + 1) % 2) * 2

    # Character body (rectangle that gets wider/longer)
    w = 4 + offset
    h = 3 + (anim_idx * 1) + offset
    x = base_x + (GRID_W * CELL_WIDTH - w * 2) // 2
    y = base_y + (GRID_H * CELL_HEIGHT - h * 2) // 2

    # Outline
    draw.rectangle([x - 1, y - 1, x + w * 2 + 2, y + h * 2 + 2], fill=CHAR_OUTLINE)

    # Fill
    draw.rectangle([x, y, x + w * 2, y + h * 2], fill=CHAR_COLOR)

    # Eyes (for character reference)
    eye_y = y
    draw.point([x + 2, eye_y], fill=(0, 0, 0))
    draw.point([x + w * 2 - 2, eye_y], fill=(0, 0, 0))


def create_ai_sprite_sheet():
    """Create a sprite sheet with magenta transparency for testing."""

    # Calculate total dimensions
    cols = FRAMES_PER_ANIM
    rows = NUM_ANIMS
    total_width = cols * GRID_W * CELL_WIDTH
    total_height = rows * GRID_H * CELL_HEIGHT

    # Create image with magenta background
    img = Image.new("RGB", (total_width, total_height), TRANSPARENT)
    draw = ImageDraw.Draw(img)

    # Draw each frame
    for anim_idx in range(NUM_ANIMS):
        for frame_idx in range(FRAMES_PER_ANIM):
            # Calculate position
            col = frame_idx
            row = anim_idx
            x = col * GRID_W * CELL_WIDTH
            y = row * GRID_H * CELL_HEIGHT

            # Draw character placeholder
            create_test_character(draw, x, y, anim_idx, frame_idx)

    # Ensure tests directory exists
    os.makedirs("tests/fixtures", exist_ok=True)

    # Save
    output_path = "tests/fixtures/ai_sprite_sheet.png"
    img.save(output_path)
    print(f"Created AI sprite sheet fixture: {output_path}")
    print(f"  Size: {total_width}x{total_height} pixels")
    print(f"  Grid: {cols}x{rows} frames = {cols * rows} total frames")
    print(f"  Cell: {GRID_W}x{GRID_H} chars per frame")
    print(f"  Background: Magenta (255, 0, 255)")

    return output_path


if __name__ == "__main__":
    create_ai_sprite_sheet()
