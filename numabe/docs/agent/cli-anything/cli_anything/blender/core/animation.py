"""Animation — keyframes, timeline, playback range."""

from cli_anything.blender.core.bridge import BlenderBridge


def list_keyframes(blend_file, object_name, blender_path=None):
    """List keyframes for an object."""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import bpy
obj = bpy.data.objects.get({object_name!r})
if obj is None:
    raise ValueError("Object '{object_name!s}' not found")
keyframes = []
if obj.animation_data and obj.animation_data.action:
    for fc in obj.animation_data.action.fcurves:
        for kp in fc.keyframe_points:
            keyframes.append({{
                "data_path": fc.data_path,
                "array_index": fc.array_index,
                "frame": kp.co[0],
                "value": round(kp.co[1], 4),
                "interpolation": kp.interpolation,
            }})
_data = {{
    "object": obj.name,
    "has_animation": obj.animation_data is not None,
    "keyframes": keyframes,
    "count": len(keyframes),
}}
""")


def insert_keyframe(blend_file, object_name, frame, data_path="location",
                    value=None, save=True, blender_path=None):
    """Insert a keyframe on an object at a given frame."""
    save_code = "bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)" if save else ""
    value_code = ""
    if value is not None:
        value_code = f"setattr(obj, {data_path!r}, {value})"

    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import bpy
obj = bpy.data.objects.get({object_name!r})
if obj is None:
    raise ValueError("Object '{object_name!s}' not found")
bpy.context.scene.frame_set({frame})
{value_code}
obj.keyframe_insert(data_path={data_path!r}, frame={frame})
{save_code}
_data = {{
    "object": obj.name,
    "frame": {frame},
    "data_path": {data_path!r},
    "status": "keyframe_inserted",
}}
""")


def set_frame_range(blend_file, start, end, save=True, blender_path=None):
    """Set the animation frame range."""
    save_code = "bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)" if save else ""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute(f"""
import bpy
bpy.context.scene.frame_start = {start}
bpy.context.scene.frame_end = {end}
{save_code}
_data = {{
    "frame_start": {start},
    "frame_end": {end},
    "status": "range_set",
}}
""")


def get_timeline(blend_file, blender_path=None):
    """Get timeline information."""
    bridge = BlenderBridge(blend_file=blend_file, blender_path=blender_path)
    return bridge.execute("""
import bpy
scene = bpy.context.scene
animated = []
for obj in bpy.data.objects:
    if obj.animation_data and obj.animation_data.action:
        fc_count = len(obj.animation_data.action.fcurves)
        kp_count = sum(len(fc.keyframe_points) for fc in obj.animation_data.action.fcurves)
        animated.append({
            "name": obj.name,
            "action": obj.animation_data.action.name,
            "fcurves": fc_count,
            "keyframes": kp_count,
        })
_data = {
    "frame_start": scene.frame_start,
    "frame_end": scene.frame_end,
    "frame_current": scene.frame_current,
    "fps": scene.render.fps,
    "duration_seconds": round((scene.frame_end - scene.frame_start + 1) / scene.render.fps, 2),
    "animated_objects": animated,
}
""")
