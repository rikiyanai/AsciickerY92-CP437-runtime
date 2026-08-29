"""
Create multi-angle test fixture for end-to-end verification.

Specification: 8 angles × 2 animations × 4 frames = 64 total frames
Grid: 8 columns (angles) × 8 rows (anims × frames)
Cell: 8×4 chars = 96×48px per frame
"""

from PIL import Image, ImageDraw
import math
import os


def create_multi_angle_fixture():
    """Create 8×8 sprite sheet for testing."""
    
    CELL_WIDTH = 12  # pixels per character
    CELL_HEIGHT = 12
    GRID_W = 8  # character width
    GRID_H = 4  # character height
    ANGLES = 8
    ANIMS = 2
    FRAMES_PER_ANIM = 4
    
    # Calculate total dimensions
    cols = ANGLES
    rows = ANIMS * FRAMES_PER_ANIM
    frame_width = GRID_W * CELL_WIDTH
    frame_height = GRID_H * CELL_HEIGHT
    
    total_width = cols * frame_width
    total_height = rows * frame_height
    
    print(f"Creating multi-angle fixture: {total_width}x{total_height}")
    print(f"  Grid: {cols}x{rows} frames")
    print(f"  Total frames: {cols * rows}")
    print(f"  Cell size: 8×4 chars = {frame_width}x{frame_height}px per frame")
    
    # Create sprite sheet
    sheet = Image.new('RGB', (total_width, total_height), (255, 0, 255))  # Magenta background
    draw = ImageDraw.Draw(sheet)
    
    # Draw 64 frames with different content per frame
    for anim_idx in range(ANIMS):
        for frame_idx in range(FRAMES_PER_ANIM):
            for angle_idx in range(ANGLES):
                # Calculate frame position
                col = angle_idx
                row = anim_idx * FRAMES_PER_ANIM + frame_idx
                
                frame_x = col * frame_width
                frame_y = row * frame_height
                
                # Create unique character view for this combination
                # Use different shapes/positions to distinguish frames
                
                # Animate Y position (bobbing)
                bob_offset = math.sin((frame_idx / FRAMES_PER_ANIM) * 2 * math.pi) * 4
                
                # Animate width for different animation (anim 0 vs anim 1)
                if anim_idx == 1:
                    width_scale = 2.0 + math.sin((frame_idx / FRAMES_PER_ANIM) * 2 * math.pi)
                else:
                    width_scale = 1.0
                
                w_anim = int(frame_width * 0.3 * width_scale)
                h_anim = int(frame_height * 0.4)
                x_center = frame_x + (frame_width - w_anim) // 2
                y_center = frame_y + (frame_height - h_anim) // 2 + int(bob_offset)
                
                # Rotate angle indicator (triangle pointing in angle direction)
                angle_deg = angle_idx * 45  # 0, 45, 90, 135, 180, 225, 270, 315
                angle_rad = math.radians(angle_deg + 90)
                dx = int(math.cos(angle_rad) * 10)
                dy = int(math.sin(angle_rad) * 10)
                
                # Draw angle indicator
                draw.line([x_center, y_center, x_center + dx, y_center + dy], fill=(200, 100, 100))
                
                # Draw simple character shape that varies by angle
                # Use polygon to create unique shapes per angle
                if angle_idx == 0:  # Front view - square with eyes
                    draw.rectangle([x_center - w_anim//2, y_center - h_anim//2,
                                  x_center + w_anim//2, y_center + h_anim//2],
                                 fill=(150, 150, 150))
                    # Eyes
                    draw.ellipse([x_center - w_anim//4, y_center - 3,
                                x_center - w_anim//6, y_center + 3],
                               fill=(0, 0, 0))
                elif angle_idx == 1:  # 45° - narrow rectangle
                    draw.rectangle([x_center - w_anim//3, y_center - h_anim//2,
                                  x_center + w_anim//3, y_center + h_anim//2],
                                 fill=(180, 160, 140))
                elif angle_idx == 2:  # Side view (left) - thin rectangle
                    draw.rectangle([x_center - w_anim//4, y_center - h_anim//3,
                                  x_center + w_anim//4, y_center + h_anim//3],
                                 fill=(160, 140, 120))
                elif angle_idx == 3:  # 135° - small diamond
                    draw.polygon([x_center, y_center - h_anim//3,
                                x_center + w_anim//3, y_center,
                                x_center, y_center + h_anim//3],
                              fill=(140, 120, 100))
                elif angle_idx == 4:  # Back view - diamond shape
                    draw.polygon([x_center, y_center - h_anim//2,
                                x_center + w_anim//3, y_center,
                                x_center, y_center + h_anim//2,
                                x_center - w_anim//3, y_center],
                              fill=(120, 100, 80))
                elif angle_idx == 5:  # 225° - small triangle
                    draw.polygon([x_center, y_center - h_anim//2,
                                x_center + w_anim//3, y_center,
                                x_center - w_anim//3, y_center + h_anim//2],
                              fill=(100, 80, 60))
                elif angle_idx == 6: # 270° - circle view
                    draw.ellipse([x_center - min(w_anim, h_anim)//4,
                                y_center - min(w_anim, h_anim)//4,
                                x_center + min(w_anim, h_anim)//4,
                                y_center + min(w_anim, h_anim)//4],
                               fill=(80, 60, 40))
                else: # 315° - trapezoid
                    draw.polygon([x_center, y_center - h_anim//3,
                                x_center + w_anim//4, y_center - h_anim//2,
                                x_center - w_anim//4, y_center + h_anim//2,
                                x_center, y_center - h_anim//2],
                              fill=(60, 40, 20))
    
    # Ensure output directory exists
    os.makedirs('tests/fixtures', exist_ok=True)
    
    # Save fixture
    output_path = 'tests/fixtures/multi_angle_sheet.png'
    sheet.save(output_path)
    
    print(f"✓ Created multi-angle fixture: {output_path}")
    print(f"  Size: {total_width}×{total_height} pixels")
    print(f"  Layout: 8 columns × 8 rows = 64 frames total")
    print(f"  Cell: 8×4 chars = 96×48px per frame")
    print(f"  Background: Magenta (255, 0, 255)")
    
    return output_path


if __name__ == '__main__':
    create_multi_angle_fixture()
