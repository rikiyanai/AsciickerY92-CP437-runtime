"""
High-level Blender rendering interface.

Provides simple API for rendering sprites from Blender scenes via subprocess or MCP.
"""

import os
import subprocess
from typing import Optional, List, Tuple
from .blender_utils import get_blender_bin


class BlenderRenderError(Exception):
    """Custom exception for Blender render errors."""

    pass


class BlenderRenderInterface:
    """High-level interface for Blender sprite rendering."""

    def __init__(self, blender_path: Optional[str] = None):
        """
        Initialize Blender render interface.

        Args:
            blender_path: Optional path to Blender executable.
                         Auto-detected if not provided.
        """
        self.blender_path = blender_path or get_blender_bin()
        if not self.blender_path:
            raise BlenderRenderError(
                "Blender not found. Please install Blender or set BLENDER_BIN environment variable."
            )

    def render_character(
        self,
        object_name: str,
        angles: int = 8,
        anims: List[int] = None,
        output_path: str = "sprite_sheet.png",
        cell_size: Tuple[int, int] = (12, 12),
        scale_factor: int = 4,
        mode: str = "sprite_sheet",
    ) -> str:
        """
        Render a character/object to sprite sheet.

        Args:
            object_name: Name of object in Blender scene
            angles: Number of angles to render (8 for characters, 1 for items)
            anims: List of animation frame counts (e.g., [4, 4] for 2 anims)
            output_path: Output PNG path
            cell_size: Character size in cells (width, height)
            scale_factor: Render scale multiplier (4 = 48px per 12px cell)
            mode: 'sprite_sheet' or 'single_frames'

        Returns:
            Path to generated sprite sheet

        Raises:
            BlenderRenderError: If render fails
        """
        # Default animations if not specified
        if anims is None:
            anims = [4]  # Default 1 animation with 4 frames

        # Create temp directory for output
        import tempfile

        temp_dir = tempfile.mkdtemp()

        # Construct render script arguments
        # Note: render_turntable.py is now a wrapper that forwards to render_unified.py
        script_path = os.path.join(os.path.dirname(__file__), "blender", "render_turntable.py")

        cmd = [
            self.blender_path,
            "-b",  # Background mode
            "--factory-startup",
            "-P",
            script_path,
            "--",
            "--object",
            object_name,
            "--angles",
            str(angles),
            "--output",
            temp_dir,
            "--scale",
            str(scale_factor),
        ]

        try:
            # Run Blender
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minute timeout
            )

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "Unknown error"
                raise BlenderRenderError(f"Blender render failed: {error_msg}")

            # Find sprite sheet in temp directory
            sprite_sheet = os.path.join(temp_dir, "sprite_sheet.png")
            if not os.path.exists(sprite_sheet):
                raise BlenderRenderError(f"Sprite sheet not generated: {sprite_sheet}")

            # Move to output location
            import shutil

            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            shutil.move(sprite_sheet, output_path)

            # Cleanup temp
            shutil.rmtree(temp_dir, ignore_errors=True)

            return output_path

        except subprocess.TimeoutExpired:
            raise BlenderRenderError("Blender render timed out")
        except FileNotFoundError as e:
            raise BlenderRenderError(f"Blender not found: {self.blender_path}")
        except Exception as e:
            raise BlenderRenderError(f"Render failed: {str(e)}")

    def render_item(
        self,
        object_name: str,
        output_path: str = "item_sheet.png",
        cell_size: Tuple[int, int] = (1, 1),
        scale_factor: int = 12,
    ) -> str:
        """
        Render a single-frame item (1 angle, 1 animation, 1 frame).

        Args:
            object_name: Name of object in Blender scene
            output_path: Output PNG path
            cell_size: Item size in cells (width, height)
            scale_factor: Render scale multiplier

        Returns:
            Path to generated item sprite
        """
        return self.render_character(
            object_name=object_name,
            angles=1,
            anims=[1],
            output_path=output_path,
            cell_size=cell_size,
            scale_factor=scale_factor,
            mode="sprite_sheet",
        )

    def check_blender_available(self) -> bool:
        """Check if Blender is available."""
        try:
            result = subprocess.run(
                [self.blender_path, "--factory-startup", "--version"], capture_output=True, timeout=10
            )
            return result.returncode == 0
        except Exception:
            return False


# Convenience functions for direct use


def render_character(object_name, angles=8, anims=None, output_path="sprite_sheet.png"):
    """
    Convenience function to render a character.

    Args:
        object_name: Name of object in Blender scene
        angles: Number of angles to render
        anims: List of animation frame counts
        output_path: Output PNG path

    Returns:
        Path to generated sprite sheet
    """
    interface = BlenderRenderInterface()
    return interface.render_character(object_name, angles, anims, output_path)


def render_item(object_name, output_path="item.png"):
    """
    Convenience function to render a single-frame item.

    Args:
        object_name: Name of object in Blender scene
        output_path: Output PNG path

    Returns:
        Path to generated item sprite
    """
    interface = BlenderRenderInterface()
    return interface.render_item(object_name, output_path)


if __name__ == "__main__":
    # Test rendering (for manual testing)
    import sys

    if len(sys.argv) > 1:
        obj_name = sys.argv[1]
        print(f"Rendering {obj_name}...")
        try:
            output = render_character(obj_name)
            print(f"Success! Output: {output}")
        except BlenderRenderError as e:
            print(f"Error: {e}")
            sys.exit(1)
    else:
        print("Usage: python blender_render.py <object_name>")
        sys.exit(1)
