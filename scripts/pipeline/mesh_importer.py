"""
mesh_importer.py -- Orchestrator for 3D mesh file import and conversion.

ARCHITECTURE:
    Handles the "mesh" source type in the asset pipeline. Converts 3D model
    files (OBJ, STL, FBX, GLTF, GLB, PLY) into .blend files via headless
    Blender, then hands off to the existing Blender render subprocess.

    [FLOW:MESH-IMPORT] Data flow:
        mesh file --> Blender headless import --> .blend file
                  --> user checkpoint (orientation confirmation)
                  --> existing Blender render pipeline

KEY EXPORTS:
    - MeshImporter: Main orchestrator class

PIPELINE CONTEXT:
    [PIPELINE:GENERATE] Runs before the Blender subprocess render.
    Called by generator.py when source_type="mesh".
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Tuple

from .schemas import MESH_EXTENSIONS

logger = logging.getLogger(__name__)

# Staging directory for converted .blend files
DEFAULT_OUTPUT_DIR = "scripts/pipeline/staging/renders"


class MeshImporter:
    """Orchestrates 3D mesh import and conversion to .blend.

    [FLOW:MESH-IMPORT] Conversion flow:
        1. Validate input mesh file
        2. Run Blender headless to import and save as .blend
        3. Present user checkpoint with scene info
        4. Return .blend path and object name for downstream rendering
    """

    def __init__(self, mesh_path: str, output_dir: str = DEFAULT_OUTPUT_DIR):
        self.mesh_path = os.path.abspath(mesh_path)
        self.output_dir = output_dir

    def validate(self) -> None:
        """Validate the input mesh file exists and has a supported extension."""
        if not os.path.exists(self.mesh_path):
            raise FileNotFoundError(f"Mesh file not found: {self.mesh_path}")

        ext = Path(self.mesh_path).suffix.lower()
        if ext not in MESH_EXTENSIONS:
            supported = ', '.join(sorted(MESH_EXTENSIONS))
            raise ValueError(f"Unsupported mesh format: {ext}. Supported: {supported}")

    def _find_blender(self) -> str:
        """Locate the Blender executable."""
        from scripts.blender_utils import get_blender_bin
        blender_bin = get_blender_bin()
        if not blender_bin:
            raise FileNotFoundError(
                "Blender not found. Install Blender or set BLENDER_BIN environment variable."
            )
        return blender_bin

    def convert(self) -> dict:
        """Run Blender headless import and return scene info.

        Returns:
            dict with keys: object_name, vertex_count, bbox_min, bbox_max,
                            size, blend_path, object_count
        """
        self.validate()

        blender_bin = self._find_blender()

        # Determine output .blend path
        stem = Path(self.mesh_path).stem
        os.makedirs(self.output_dir, exist_ok=True)
        blend_path = os.path.join(self.output_dir, f"{stem}.blend")

        # Run the Blender headless import script
        import_script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "blender", "import_mesh.py"
        )

        cmd = [
            blender_bin, "-b", "--factory-startup",
            "-P", import_script,
            "--",
            "--input", self.mesh_path,
            "--output", blend_path,
        ]

        logger.info(f"Running Blender mesh import: {stem}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            logger.error(f"Blender stdout: {result.stdout[-2000:]}")
            logger.error(f"Blender stderr: {result.stderr[-2000:]}")
            raise RuntimeError(f"Blender mesh import failed (exit code {result.returncode})")

        # Parse JSON output from the import script
        scene_info = self._parse_scene_info(result.stdout)
        if scene_info is None:
            raise RuntimeError(
                "Blender import completed but no scene info found in output. "
                f"Last 500 chars: {result.stdout[-500:]}"
            )

        return scene_info

    def _parse_scene_info(self, stdout: str) -> dict | None:
        """Extract JSON scene info from Blender stdout."""
        marker = "MESH_IMPORT_JSON:"
        for line in stdout.splitlines():
            if line.startswith(marker):
                return json.loads(line[len(marker):])
        return None

    def checkpoint_cli(self, scene_info: dict) -> str:
        """Present interactive CLI checkpoint for orientation review.

        Returns:
            'proceed', 'open', or 'cancel'
        """
        size = scene_info.get("size", [0, 0, 0])
        print("\n=== Mesh Import Complete ===")
        print(f"  Source:    {Path(self.mesh_path).name}")
        print(f"  Object:   {scene_info['object_name']}")
        if scene_info.get("object_count", 1) > 1:
            print(f"  Parts:    {scene_info['object_count']} mesh objects")
        print(f"  Vertices: {scene_info['vertex_count']:,}")
        print(f"  Size:     X={size[0]:.1f}, Y={size[1]:.1f}, Z={size[2]:.1f}")
        print(f"  Blend:    {scene_info['blend_path']}")
        print()
        print("  [P]roceed  [O]pen in Blender to adjust  [C]ancel")
        print()

        while True:
            choice = input("  > ").strip().lower()
            if choice in ('p', 'proceed', ''):
                return 'proceed'
            if choice in ('o', 'open'):
                return 'open'
            if choice in ('c', 'cancel'):
                return 'cancel'
            print("  Please enter P, O, or C")

    def run(self, interactive: bool = True) -> Tuple[str, str]:
        """Full flow: convert mesh + optional checkpoint.

        Args:
            interactive: If True, show CLI checkpoint. If False, auto-proceed.

        Returns:
            (blend_path, object_name) tuple for downstream Blender rendering.
        """
        scene_info = self.convert()
        blend_path = scene_info["blend_path"]
        object_name = scene_info["object_name"]

        if not interactive:
            logger.info(f"Non-interactive mode: auto-proceeding with {object_name}")
            return blend_path, object_name

        while True:
            choice = self.checkpoint_cli(scene_info)

            if choice == 'proceed':
                return blend_path, object_name

            if choice == 'open':
                blender_bin = self._find_blender()
                print(f"\n  Opening Blender: {blender_bin} {blend_path}")
                print("  Adjust orientation, save, then press Enter to continue...")
                subprocess.Popen([blender_bin, blend_path])
                input("  Press Enter when done > ")
                # Re-probe the .blend to get updated object name
                scene_info = self._reprobe_blend(blend_path)
                object_name = scene_info["object_name"]
                continue

            if choice == 'cancel':
                raise RuntimeError("Mesh import cancelled by user")

    def _reprobe_blend(self, blend_path: str) -> dict:
        """Re-probe a .blend file to get updated object info after user edits."""
        blender_bin = self._find_blender()

        probe_script = (
            "import bpy, json, sys\n"
            "meshes = [o for o in bpy.data.objects if o.type == 'MESH']\n"
            "if not meshes:\n"
            "    print('MESH_IMPORT_JSON:' + json.dumps({'error': 'No meshes found'}))\n"
            "    sys.exit(1)\n"
            "primary = max(meshes, key=lambda o: len(o.data.vertices))\n"
            "total_verts = sum(len(o.data.vertices) for o in meshes)\n"
            "info = {'object_name': primary.name, 'vertex_count': total_verts,\n"
            "        'object_count': len(meshes), 'blend_path': bpy.data.filepath,\n"
            "        'size': [0, 0, 0], 'bbox_min': [0, 0, 0], 'bbox_max': [0, 0, 0]}\n"
            "print('MESH_IMPORT_JSON:' + json.dumps(info))\n"
        )

        result = subprocess.run(
            [blender_bin, "-b", blend_path, "--python-expr", probe_script],
            capture_output=True, text=True, timeout=30,
        )

        scene_info = self._parse_scene_info(result.stdout)
        if scene_info is None:
            raise RuntimeError("Failed to re-probe .blend file after editing")
        return scene_info
