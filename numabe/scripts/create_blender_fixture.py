"""
Create Blender-rendered test fixture.

Generates a simple sprite sheet that simulates Blender output for testing.
Since Blender may not be installed, this creates a mock fixture.
"""

from PIL import Image, ImageDraw
import os


def create_blender_mock_fixture():
    """Create a mock Blender-rendered sprite sheet for testing.

    Layout: 8 frames (8 angles × 1 anim × 1 frame)
    Grid: 8 columns × 1 row
    Each frame: 96×48px (8×4 cells at 12px/cell)
    Total: 768×48px
    Background: Magenta (255, 0, 255)
    """

    CELL_WIDTH = 12
    CELL_HEIGHT = 12
    GRID_W = 8
    GRID_H = 4
    ANGLES = 8

    # Create sprite sheet
    total_width = ANGLES * GRID_W * CELL_WIDTH
    total_height = GRID_H * CELL_HEIGHT

    img = Image.new("RGB", (total_width, total_height), (255, 0, 255))
    draw = ImageDraw.Draw(img)

    # Draw 8 different character silhouettes (simulating turntable)
    for angle in range(ANGLES):
        # Calculate frame position
        frame_x = angle * GRID_W * CELL_WIDTH

        # Calculate rotation angle in radians (simplified for 2D mock)
        import math

        theta = math.radians(angle * 45)  # 0, 45, 90, 135, etc.

        # Create a "rotating" character shape
        # Draw different views: front, 45°, side, etc.
        char_w, char_h = int(8 * CELL_WIDTH * 0.6), int(4 * CELL_HEIGHT * 0.7)
        char_x = frame_x + (GRID_W * CELL_WIDTH - char_w) // 2
        char_y = (total_height - char_h) // 2

        # Get rotation angle for display
        angle_deg = angle * 45

        # Draw character based on angle
        if angle_deg == 0:  # Front view
            draw.rectangle(
                [char_x, char_y, char_x + char_w, char_y + char_h],
                outline=(200, 200, 200),
            )
            # Eyes
            draw.polygon(
                [
                    char_x + char_w * 0.3,
                    char_y + char_h * 0.3,
                    char_x + char_w * 0.45,
                    char_y + char_h * 0.25,
                    char_x + char_w * 0.6,
                    char_y + char_h * 0.3,
                ],
                fill=(50, 50, 50),
            )
            draw.polygon(
                [
                    char_x + char_w * 0.4,
                    char_y + char_h * 0.3,
                    char_x + char_w * 0.6,
                    char_y + char_h * 0.3,
                ],
                fill=(50, 50, 50),
            )

        elif angle_deg == 45:  # 45° view (profile-ish)
            draw.rectangle(
                [char_x, char_y, char_x + char_w * 0.7, char_y + char_h],
                outline=(200, 200, 200),
            )
            # Single eye
            draw.ellipse(
                [
                    char_x + char_w * 0.3,
                    char_y + char_h * 0.25,
                    char_x + char_w * 0.4,
                    char_y + char_h * 0.35,
                ],
                fill=(50, 50, 50),
            )

        elif angle_deg == 90:  # Side view (left)
            draw.rectangle(
                [char_x, char_y, char_x + char_w * 0.5, char_y + char_h],
                outline=(200, 200, 200),
            )
            # Profile eye
            draw.ellipse(
                [
                    char_x + char_w * 0.35,
                    char_y + char_h * 0.25,
                    char_x + char_w * 0.45,
                    char_y + char_h * 0.35,
                ],
                fill=(50, 50, 50),
            )

        elif angle_deg == 135:  # 135° view (back-ish)
            draw.rectangle(
                [char_x, char_y, char_x + char_w * 0.7, char_y + char_h],
                outline=(200, 200, 200),
            )
            # Back view (no eyes)
            draw.polygon(
                [
                    char_x + char_w * 0.2,
                    char_y + char_h * 0.2,
                    char_x + char_w * 0.5,
                    char_y + char_h * 0.25,
                    char_x + char_w * 0.2,
                    char_y + char_h * 0.3,
                ],
                fill=(50, 50, 50),
            )

        elif angle_deg == 180:  # Back view
            draw.rectangle(
                [char_x, char_y, char_x + char_w, char_y + char_h],
                outline=(200, 200, 200),
            )
            # Back shape (curved)
            draw.polygon(
                [
                    char_x + char_w * 0.2,
                    char_y + char_h * 0.2,
                    char_x + char_w * 0.5,
                    char_y + char_h * 0.25,
                    char_x + char_w * 0.8,
                    char_y + char_h * 0.2,
                ],
                fill=(50, 50, 50),
            )

        elif angle_deg == 225:  # 225° (back-right)
            draw.rectangle(
                [char_x + char_w * 0.3, char_y, char_x + char_w, char_y + char_h],
                outline=(200, 200, 200),
            )

        elif angle_deg == 270:  # Right side view
            draw.rectangle(
                [char_x + char_w * 0.5, char_y, char_x + char_w, char_y + char_h],
                outline=(200, 200, 200),
            )
            # Right eye
            draw.ellipse(
                [
                    char_x + char_w * 0.55,
                    char_y + char_h * 0.25,
                    char_x + char_w * 0.65,
                    char_y + char_h * 0.35,
                ],
                fill=(50, 50, 50),
            )

        else:  # 315° (front-right)
            draw.rectangle(
                [char_x + char_w * 0.3, char_y, char_x + char_w, char_y + char_h],
                outline=(200, 200, 200),
            )
            draw.ellipse(
                [
                    char_x + char_w * 0.6,
                    char_y + char_h * 0.25,
                    char_x + char_w * 0.7,
                    char_y + char_h * 0.35,
                ],
                fill=(50, 50, 50),
            )

    # Save
    os.makedirs("tests/fixtures", exist_ok=True)
    output_path = "tests/fixtures/blender_rendered_sprite.png"
    img.save(output_path)

    print(f"Created Blender mock fixture: {output_path}")
    print(f"  Size: {total_width}x{total_height} pixels")
    print(f"  Grid: {ANGLES} frames = {ANGLES} angles")
    print(f"  Cell: {GRID_W}x{GRID_H} chars per frame")
    print(f"  Background: Magenta (255, 0, 255)")
    print(f"  Note: This is a mock fixture since Blender may not be available")

    return output_path


if __name__ == "__main__":
    create_blender_mock_fixture()
