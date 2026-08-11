# Render to XP -- Multi-angle render + XP conversion pipeline
# [DEPENDENCY:BLENDER] - Requires Blender 4.0+
# [DEPENDENCY:PIL] - Uses Pillow for sprite sheet assembly (via SpriteSheetMaker)

import bpy
import math
import os
import subprocess
import tempfile
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty

from io_asciicker import path_utils


def _get_repo_root():
    """Resolve the repository root from the addon's real path."""
    real_file = os.path.realpath(__file__)
    root = path_utils.find_repo_root(real_file)
    if root:
        return root
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))


def _find_python():
    """Find a suitable Python 3 interpreter for the asset pipeline.

    Blender's subprocess PATH often resolves to Apple's /usr/bin/python3
    which may be too old. Prefer Homebrew or other full installs.
    """
    import shutil
    for candidate in ("python3.14", "python3.13", "python3.12", "python3.11", "python3"):
        # Check common Homebrew paths first
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
            full = os.path.join(prefix, candidate)
            if os.path.isfile(full):
                return full
        found = shutil.which(candidate)
        if found and found != "/usr/bin/python3":
            return found
    return "python3"


def _launch_xp_tool(repo_root, xp_path):
    """Open the XP Tool viewer in a new terminal window."""
    python_bin = _find_python()
    tool_script = os.path.join(repo_root, "scripts", "asset_gen", "xp_tool.py")
    if not os.path.exists(tool_script):
        return
    import sys
    if sys.platform == "darwin":
        # macOS: open a new Terminal.app tab
        escaped = xp_path.replace('"', '\\"')
        apple_script = (
            f'tell application "Terminal" to do script '
            f'"{python_bin} {tool_script} \\"{escaped}\\""'
        )
        subprocess.Popen(["osascript", "-e", apple_script])
    else:
        # Linux: try common terminal emulators
        for term in ("gnome-terminal", "xterm", "konsole"):
            import shutil
            if shutil.which(term):
                subprocess.Popen([term, "--", python_bin, tool_script, xp_path])
                return


def _get_bounding_box_center(objects):
    """Compute the world-space bounding box center of a set of objects."""
    min_co = [float('inf')] * 3
    max_co = [float('-inf')] * 3
    for obj in objects:
        for corner in obj.bound_box:
            world_co = obj.matrix_world @ bpy.app.driver_namespace.get('__mathutils_Vector', None) or obj.matrix_world @ __import__('mathutils').Vector(corner)
            for i in range(3):
                min_co[i] = min(min_co[i], world_co[i])
                max_co[i] = max(max_co[i], world_co[i])
    return [(min_co[i] + max_co[i]) / 2.0 for i in range(3)]


def _get_bounding_radius(objects, center):
    """Compute the maximum distance from center to any bounding box corner."""
    from mathutils import Vector
    center_v = Vector(center)
    max_dist = 0.0
    for obj in objects:
        for corner in obj.bound_box:
            world_co = obj.matrix_world @ Vector(corner)
            dist = (world_co - center_v).length
            max_dist = max(max_dist, dist)
    return max_dist


def _setup_ortho_camera(context, angle_deg, center, radius, camera_obj):
    """Position orthographic camera at angle_deg around center on XY plane.

    Angle 0 = South (camera at -Y looking +Y) -- matches engine convention.
    """
    from mathutils import Vector, Euler

    angle_rad = math.radians(angle_deg)
    # South = camera at -Y, so offset by -90 deg (camera looks toward center)
    cam_x = center[0] + radius * 2.0 * math.sin(angle_rad)
    cam_y = center[1] - radius * 2.0 * math.cos(angle_rad)
    cam_z = center[2] + radius * 0.7  # slight elevation

    camera_obj.location = (cam_x, cam_y, cam_z)

    # Point camera at center
    direction = Vector(center) - Vector(camera_obj.location)
    rot_quat = direction.to_track_quat('-Z', 'Y')
    camera_obj.rotation_euler = rot_quat.to_euler()

    # Orthographic scale to fit bounding box
    camera_obj.data.type = 'ORTHO'
    camera_obj.data.ortho_scale = radius * 2.2


class ASCIICKER_OT_render_sprite_xp(bpy.types.Operator):
    """Render selected objects at multiple angles and convert to XP sprite"""
    bl_idname = "asciicker.render_sprite_xp"
    bl_label = "Render to XP"
    bl_options = {'REGISTER'}

    sprite_name: StringProperty(
        name="Sprite Name",
        description="Output sprite name (without extension)",
        default="sprite",
    )
    angles: IntProperty(
        name="Angles",
        description="Number of rotation angles (1, 4, or 8)",
        default=8,
        min=1,
        max=8,
    )
    apply_pixel_art: BoolProperty(
        name="Apply Pixel Art",
        description="Apply pixel art render settings before rendering",
        default=True,
    )
    pixel_material: EnumProperty(
        name="Pixel Material",
        items=[
            ('none', "None", "Don't create pixel art material"),
            ('simple', "Simple", "Create PixelArt_Simple material"),
            ('multiple', "Multi-Light", "Create PixelArt_MultipleLights material + tri-light"),
        ],
        default='none',
    )

    def execute(self, context):
        from . import pixel_render

        scene = context.scene
        selected = [obj for obj in context.selected_objects if obj.type == 'MESH']
        if not selected:
            self.report({'WARNING'}, "No mesh objects selected")
            return {'CANCELLED'}

        # Step 1: Apply pixel art settings
        if self.apply_pixel_art:
            try:
                pixel_render.render_settings(context)
            except Exception as e:
                self.report({'WARNING'}, f"Pixel render settings failed: {e}")

        if self.pixel_material == 'simple':
            pixel_render.single_material(context)
        elif self.pixel_material == 'multiple':
            pixel_render.multiple_material(context)
            pixel_render.lights_setup(context)

        # Step 2: Compute bounding box
        from mathutils import Vector
        min_co = Vector((float('inf'),) * 3)
        max_co = Vector((float('-inf'),) * 3)
        for obj in selected:
            for corner in obj.bound_box:
                wc = obj.matrix_world @ Vector(corner)
                for i in range(3):
                    if wc[i] < min_co[i]:
                        min_co[i] = wc[i]
                    if wc[i] > max_co[i]:
                        max_co[i] = wc[i]
        center = (min_co + max_co) / 2.0
        radius = (max_co - min_co).length / 2.0

        if radius < 0.001:
            self.report({'ERROR'}, "Selected objects have zero bounding box")
            return {'CANCELLED'}

        # Step 3: Create temp camera
        cam_data = bpy.data.cameras.new(name="ASCIICKER_RenderCam")
        cam_obj = bpy.data.objects.new("ASCIICKER_RenderCam", cam_data)
        context.collection.objects.link(cam_obj)
        scene.camera = cam_obj
        cam_data.type = 'ORTHO'

        # Transparent background
        scene.render.film_transparent = True
        scene.render.image_settings.file_format = 'PNG'
        scene.render.image_settings.color_mode = 'RGBA'

        # Step 4: Render each angle
        tmp_dir = tempfile.mkdtemp(prefix="asciicker_render_")
        angle_count = max(1, min(self.angles, 8))
        angle_step = 360.0 / angle_count

        # Get animation strips or just render current frame
        strips = list(getattr(scene, 'asciicker_sprite_sheet', {}).get('animation_strips', [])) if hasattr(scene, 'asciicker_sprite_sheet') else []

        frame_start = scene.frame_start
        frame_end = scene.frame_end
        frame_count = frame_end - frame_start + 1

        rendered_frames = []
        for angle_idx in range(angle_count):
            angle_deg = angle_idx * angle_step
            _setup_ortho_camera(context, angle_deg, center, radius, cam_obj)

            angle_dir = os.path.join(tmp_dir, f"angle_{angle_idx:02d}")
            os.makedirs(angle_dir, exist_ok=True)

            for frame in range(frame_start, frame_end + 1):
                scene.frame_set(frame)
                filepath = os.path.join(angle_dir, f"frame_{frame:04d}.png")
                scene.render.filepath = filepath
                bpy.ops.render.render(write_still=True)
                rendered_frames.append(filepath)

        # Step 5: Assemble sprite sheet using PIL
        try:
            from PIL import Image
        except ImportError:
            self.report({'ERROR'}, "Pillow not available. Install with: pip install Pillow")
            self._cleanup(cam_obj, tmp_dir)
            return {'CANCELLED'}

        # Load all frames and assemble: rows = angles, cols = frames
        rows = []
        for angle_idx in range(angle_count):
            angle_dir = os.path.join(tmp_dir, f"angle_{angle_idx:02d}")
            frame_images = []
            for frame in range(frame_start, frame_end + 1):
                filepath = os.path.join(angle_dir, f"frame_{frame:04d}.png")
                if os.path.exists(filepath):
                    frame_images.append(Image.open(filepath))
            rows.append(frame_images)

        if not rows or not rows[0]:
            self.report({'ERROR'}, "No frames rendered")
            self._cleanup(cam_obj, tmp_dir)
            return {'CANCELLED'}

        cell_w = rows[0][0].width
        cell_h = rows[0][0].height

        if cell_w % 12 != 0 or cell_h % 12 != 0:
            self.report({'WARNING'}, f"Render resolution {cell_w}x{cell_h} is not divisible by 12. XP conversion will fail. Use 48, 60, 72, 96, etc.")

        sheet_w = cell_w * frame_count
        sheet_h = cell_h * angle_count

        sheet = Image.new('RGBA', (sheet_w, sheet_h), (0, 0, 0, 0))
        for row_idx, frame_images in enumerate(rows):
            for col_idx, img in enumerate(frame_images):
                sheet.paste(img, (col_idx * cell_w, row_idx * cell_h))

        sheet_path = os.path.join(tmp_dir, f"{self.sprite_name}_sheet.png")
        sheet.save(sheet_path)

        # Step 6: Convert to XP via asset pipeline
        repo_root = _get_repo_root()
        cli_path = os.path.join(repo_root, "scripts", "asset_gen", "cli.py")

        if not os.path.exists(cli_path):
            self.report({'WARNING'}, f"Asset pipeline not found at {cli_path}. Sheet saved to: {sheet_path}")
            self._cleanup(cam_obj, None)  # keep tmp_dir
            return {'FINISHED'}

        python_bin = _find_python()
        cmd = [
            python_bin, "-m", "scripts.asset_gen.cli",
            "--non-interactive",
            "--source-type", "file",
            "--input", sheet_path,
            "--name", self.sprite_name,
            "--angles", str(angle_count),
            "--frames", str(frame_count),
            "--projs", "1",
            "--reflection-policy", "none",
            "--cell-w", str(cell_w),
            "--cell-h", str(cell_h),
            "--cols", str(frame_count),
            "--rows", str(angle_count),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=repo_root)
            if result.returncode == 0:
                xp_path = os.path.join(repo_root, "scripts", "asset_gen", "staging", "xp", f"{self.sprite_name}.xp")
                self.report({'INFO'}, f"Rendered {self.sprite_name} with {angle_count} angles, {frame_count} frames -> XP")
                if os.path.exists(xp_path):
                    _launch_xp_tool(repo_root, xp_path)
            else:
                self.report({'WARNING'}, f"Pipeline returned code {result.returncode}: {result.stderr[:200]}")
        except subprocess.TimeoutExpired:
            self.report({'ERROR'}, "Pipeline timed out after 120s")
        except FileNotFoundError:
            self.report({'WARNING'}, f"python3 not found. Sheet saved to: {sheet_path}")

        self._cleanup(cam_obj, None)  # keep tmp_dir for inspection
        return {'FINISHED'}

    def _cleanup(self, cam_obj, tmp_dir):
        """Remove temporary camera. Optionally remove temp directory."""
        if cam_obj:
            cam_data = cam_obj.data
            bpy.data.objects.remove(cam_obj, do_unlink=True)
            bpy.data.cameras.remove(cam_data)
        if tmp_dir:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)


class ASCIICKER_OT_convert_png_to_xp(bpy.types.Operator):
    """Convert an existing PNG sprite sheet to XP format"""
    bl_idname = "asciicker.convert_png_to_xp"
    bl_label = "Convert PNG to XP"
    bl_options = {'REGISTER'}

    input_path: StringProperty(
        name="Input PNG",
        description="Path to the PNG sprite sheet",
        subtype='FILE_PATH',
    )
    sprite_name: StringProperty(
        name="Sprite Name",
        description="Output sprite name",
        default="sprite",
    )
    angles: IntProperty(
        name="Angles",
        description="Number of rotation angles",
        default=8,
        min=1,
        max=8,
    )
    frames: StringProperty(
        name="Frames",
        description="Comma-separated frame counts per angle",
        default="1",
    )

    def execute(self, context):
        if not self.input_path or not os.path.exists(bpy.path.abspath(self.input_path)):
            self.report({'ERROR'}, "Input PNG path is invalid")
            return {'CANCELLED'}

        repo_root = _get_repo_root()
        cli_path = os.path.join(repo_root, "scripts", "asset_gen", "cli.py")

        if not os.path.exists(cli_path):
            self.report({'ERROR'}, f"Asset pipeline not found at {cli_path}")
            return {'CANCELLED'}

        abs_input = bpy.path.abspath(self.input_path)
        python_bin = _find_python()
        cmd = [
            python_bin, "-m", "scripts.asset_gen.cli",
            "--non-interactive",
            "--source-type", "file",
            "--input", abs_input,
            "--name", self.sprite_name,
            "--angles", str(self.angles),
            "--frames", self.frames,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=repo_root)
            if result.returncode == 0:
                xp_path = os.path.join(repo_root, "scripts", "asset_gen", "staging", "xp", f"{self.sprite_name}.xp")
                self.report({'INFO'}, f"Converted {self.sprite_name} to XP")
                if os.path.exists(xp_path):
                    _launch_xp_tool(repo_root, xp_path)
            else:
                self.report({'ERROR'}, f"Pipeline failed: {result.stderr[:300]}")
                return {'CANCELLED'}
        except subprocess.TimeoutExpired:
            self.report({'ERROR'}, "Pipeline timed out after 120s")
            return {'CANCELLED'}
        except FileNotFoundError:
            self.report({'ERROR'}, "python3 not found")
            return {'CANCELLED'}

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)


classes = (
    ASCIICKER_OT_render_sprite_xp,
    ASCIICKER_OT_convert_png_to_xp,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
