#!/usr/bin/env python3
"""Bake the Cats Godot4 Manny rig into the PNG sidecar format used by Godot."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector


PRESENTATIONS = (
    ("idle_walk", "LiteWalking-loop", 4, False),
    ("Slash1", "LiteAtk1", 6, False),
    ("Slash2", "LiteATK2", 6, False),
    ("Slash3", "LiteSpecial2", 8, False),
    ("DashSlash", "Roll", 6, False),
    ("parry", "ParryQuickL", 4, False),
    ("death", "Die", 6, True),
)
ANGLES = 8
CELL_SCALE = 8
FRAME_W_CELLS = 20
FRAME_H_CELLS = 28
FRAME_W_PX = FRAME_W_CELLS * CELL_SCALE
FRAME_H_PX = FRAME_H_CELLS * CELL_SCALE
COLOR_KEY = (255, 255, 85)


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--animation-blend", required=True)
    parser.add_argument("--weapon-source", required=True)
    parser.add_argument("--output-png", required=True)
    parser.add_argument("--output-json", required=True)
    return parser.parse_args(argv)


def clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def import_source(path: Path) -> None:
    existing = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    for obj in [obj for obj in bpy.context.scene.objects if obj not in existing]:
        if obj.name in {"Cube", "Icosphere"}:
            bpy.data.objects.remove(obj, do_unlink=True)


def load_animation_scene(path: Path) -> None:
    bpy.ops.wm.open_mainfile(filepath=str(path))
    for obj in list(bpy.context.scene.objects):
        if obj.name in {"Horizontal", "DiagL", "DiagR", "Vert"}:
            bpy.data.objects.remove(obj, do_unlink=True)
        else:
            obj.hide_render = True
    required = {action_name for _, action_name, _, _ in PRESENTATIONS}
    available = {action.name for action in bpy.data.actions}
    missing = sorted(required - available)
    if missing:
        raise RuntimeError(f"Missing required Manny actions from {path}: {missing}")


def import_weapon_source(path: Path) -> bpy.types.Object:
    existing = set(bpy.context.scene.objects)
    bpy.ops.import_scene.gltf(filepath=str(path))
    imported = [obj for obj in bpy.context.scene.objects if obj not in existing]
    sword = next((obj for obj in imported if obj.name.lower().startswith("sword")), None)
    if sword is None:
        raise RuntimeError(f"Sword mesh not found in {path}")
    for obj in imported:
        if obj != sword:
            bpy.data.objects.remove(obj, do_unlink=True)
    sword.name = "DemoSoulsSword"
    sword.scale = (2.2, 2.2, 2.2)
    return sword


def root_objects() -> list[bpy.types.Object]:
    children = {child for obj in bpy.context.scene.objects for child in obj.children}
    return [obj for obj in bpy.context.scene.objects if obj.parent is None and obj not in children]


def setup_root() -> bpy.types.Object:
    root = bpy.data.objects.new("DemoSoulsRigBakeRoot", None)
    bpy.context.collection.objects.link(root)
    for obj in list(root_objects()):
        if obj != root:
            obj.parent = root
    root.location = (0.0, 0.0, 0.0)
    root.rotation_euler = (0.0, 0.0, 0.0)
    return root


def setup_materials() -> None:
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        for slot in obj.material_slots:
            material = slot.material
            if material is not None:
                material.diffuse_color = (0.72, 0.74, 0.70, 1.0)


def setup_camera() -> None:
    camera_data = bpy.data.cameras.new("DemoSoulsBakeCamera")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = 2.9
    camera = bpy.data.objects.new("DemoSoulsBakeCamera", camera_data)
    bpy.context.collection.objects.link(camera)
    camera.location = (0.0, -5.2, 1.25)
    look_at(camera, Vector((0.0, 0.0, 1.05)))
    bpy.context.scene.camera = camera


def setup_lighting() -> None:
    world = bpy.context.scene.world or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.color = (0.0, 0.0, 0.0)
    light_data = bpy.data.lights.new("DemoSoulsBakeKey", "AREA")
    light_data.energy = 450
    light_data.size = 4.0
    light = bpy.data.objects.new("DemoSoulsBakeKey", light_data)
    bpy.context.collection.objects.link(light)
    light.location = (1.5, -3.0, 4.0)


def setup_render() -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.render.film_transparent = True
    scene.render.resolution_x = FRAME_W_PX
    scene.render.resolution_y = FRAME_H_PX
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"


def look_at(obj: bpy.types.Object, target: Vector) -> None:
    direction = target - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def armatures() -> list[bpy.types.Object]:
    return [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]


RETARGET_BONES = {
    "Ctrl_Hips": "DEF-hips",
    "Ctrl_Spine": "DEF-spine.001",
    "Ctrl_Spine1": "DEF-spine.002",
    "Ctrl_Spine2": "DEF-spine.003",
    "Ctrl_Neck": "DEF-neck",
    "Ctrl_Head": "DEF-head",
    "Ctrl_Hand_IK_Left": "DEF-hand.L",
    "Ctrl_ArmPole_IK_Left": "DEF-forearm.L",
    "Ctrl_Hand_IK_Right": "DEF-hand.R",
    "Ctrl_ArmPole_IK_Right": "DEF-forearm.R",
    "Ctrl_Foot_IK_Left": "DEF-foot.L",
    "Ctrl_Foot_IK_Right": "DEF-foot.R",
}
BONE_PATH_RE = re.compile(r'^pose\.bones\["(?P<bone>[^"]+)"\]\.(?P<property>.+)$')


def animation_armature() -> bpy.types.Object:
    rig = bpy.data.objects.get("Armature")
    if rig is not None and rig.type == "ARMATURE":
        return rig
    rig = next((obj for obj in armatures() if obj.pose and obj.pose.bones.get("mixamorig:RightHand")), None)
    if rig is None:
        raise RuntimeError("No Manny source animation armature found")
    return rig


def render_armature() -> bpy.types.Object:
    rig = bpy.data.objects.get("godot_rig")
    if rig is not None and rig.type == "ARMATURE":
        return rig
    rig = next((obj for obj in armatures() if obj.pose and obj.pose.bones.get("DEF-hand.R")), None)
    if rig is None:
        raise RuntimeError("No Manny render armature found")
    return rig


def sampled_action_frame(action: bpy.types.Action, frame_index: int, frame_count: int, reverse: bool) -> float:
    start, end = action.frame_range
    if frame_count <= 1:
        return end if reverse else start
    t = frame_index / float(frame_count - 1)
    if reverse:
        t = 1.0 - t
    return start + ((end - start) * t)


def reset_pose() -> None:
    armature = render_armature()
    for bone in armature.pose.bones:
        bone.rotation_mode = "XYZ"
        bone.location = (0.0, 0.0, 0.0)
        bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        bone.rotation_euler = (0.0, 0.0, 0.0)
        bone.scale = (1.0, 1.0, 1.0)
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.context.view_layer.update()


def apply_action(
    action_name: str,
    frame: int,
    frame_count: int,
    reverse: bool,
) -> None:
    reset_pose()
    action = bpy.data.actions.get(action_name)
    if action is None:
        raise RuntimeError(f"Missing source action {action_name}")
    resolved = sampled_action_frame(action, frame, frame_count, reverse)
    source_rig = animation_armature()
    source_rig.animation_data_clear()
    source_rig.animation_data_create()
    source_rig.animation_data.action = action
    bpy.context.scene.frame_set(int(round(resolved)))
    bpy.context.view_layer.update()
    retarget_action_channels(action, resolved)


def retarget_action_channels(action: bpy.types.Action, frame: float) -> None:
    target_rig = render_armature()
    sampled: dict[str, dict[str, list[float | None]]] = {}
    for fcurve in action.fcurves:
        match = BONE_PATH_RE.match(fcurve.data_path)
        if match is None:
            continue
        source_name = match.group("bone")
        if source_name not in RETARGET_BONES:
            continue
        prop = match.group("property")
        if prop not in {"location", "rotation_euler"}:
            continue
        sampled.setdefault(source_name, {}).setdefault(prop, [None, None, None])
        if 0 <= fcurve.array_index < 3:
            sampled[source_name][prop][fcurve.array_index] = float(fcurve.evaluate(frame))
    for source_name, channels in sampled.items():
        target_name = RETARGET_BONES[source_name]
        target_bone = target_rig.pose.bones.get(target_name)
        if target_bone is None:
            continue
        target_bone.rotation_mode = "XYZ"
        rotation = channels.get("rotation_euler")
        if rotation is not None and all(value is not None for value in rotation):
            target_bone.rotation_euler = tuple(rotation)  # type: ignore[arg-type]
        location = channels.get("location")
        if source_name == "Ctrl_Hips" and location is not None and all(value is not None for value in location):
            target_bone.location = (location[0] * 0.45, location[1] * 0.45, location[2] * 0.45)  # type: ignore[index]
    bpy.context.view_layer.update()


def attach_sword(sword: bpy.types.Object) -> None:
    armature = render_armature()
    hand = armature.pose.bones.get("DEF-hand.R")
    if hand is None:
        return
    hand_world = armature.matrix_world @ hand.matrix
    pivot = Matrix.Translation((0.0, 0.08, 0.02))
    rotate = Matrix.Rotation(math.radians(-90.0), 4, "X") @ Matrix.Rotation(math.radians(90.0), 4, "Z")
    sword.matrix_world = hand_world @ pivot @ rotate


def render_frame(
    root: bpy.types.Object,
    sword: bpy.types.Object,
    presentation: str,
    action_name: str,
    frame: int,
    frame_count: int,
    reverse: bool,
    yaw: int,
    path: Path,
) -> None:
    apply_action(action_name, frame, frame_count, reverse)
    attach_sword(sword)
    root.rotation_euler = (0.0, 0.0, yaw * math.tau / ANGLES)
    bpy.context.view_layer.update()
    bpy.context.scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def read_pixels(path: Path) -> list[float]:
    image = bpy.data.images.load(str(path))
    pixels = list(image.pixels[:])
    bpy.data.images.remove(image)
    return pixels


def write_atlas(frame_pixels: dict[tuple[str, int, int], list[float]], output_path: Path) -> None:
    rows = sum(frame_count for _, _, frame_count, _ in PRESENTATIONS)
    atlas_w = FRAME_W_PX * ANGLES
    atlas_h = FRAME_H_PX * rows
    pixels = [0.0] * (atlas_w * atlas_h * 4)
    row_index = 0
    for presentation, _, frame_count, _ in PRESENTATIONS:
        for frame in range(frame_count):
            for yaw in range(ANGLES):
                src = frame_pixels[(presentation, frame, yaw)]
                x0 = yaw * FRAME_W_PX
                y0 = row_index * FRAME_H_PX
                for y in range(FRAME_H_PX):
                    for x in range(FRAME_W_PX):
                        src_i = (y * FRAME_W_PX + x) * 4
                        dst_i = ((y0 + y) * atlas_w + (x0 + x)) * 4
                        pixels[dst_i : dst_i + 4] = src[src_i : src_i + 4]
            row_index += 1
    atlas = bpy.data.images.new("demo_manny-souls", atlas_w, atlas_h, alpha=True)
    atlas.pixels = pixels
    atlas.filepath_raw = str(output_path)
    atlas.file_format = "PNG"
    atlas.save()
    bpy.data.images.remove(atlas)


def source_cell_facts(frame_x: int, frame_y: int) -> list[dict[str, object]]:
    facts = []
    for cy in range(FRAME_H_CELLS):
        for cx in range(FRAME_W_CELLS):
            px = frame_x + cx * CELL_SCALE
            py = frame_y + cy * CELL_SCALE
            facts.append({
                "source_cell": [cx, cy],
                "local_pixel_rect": [cx * CELL_SCALE, cy * CELL_SCALE, CELL_SCALE, CELL_SCALE],
                "uv_rect_px": [px, py, CELL_SCALE, CELL_SCALE],
            })
    return facts


def write_sidecar(
    source_path: Path,
    animation_blend_path: Path,
    weapon_source_path: Path,
    output_png: Path,
    output_json: Path,
) -> None:
    total_rows = sum(frame_count for _, _, frame_count, _ in PRESENTATIONS)
    rows_seen = 0
    frames = []
    for presentation, action_name, frame_count, reverse in PRESENTATIONS:
        for frame in range(frame_count):
            for yaw in range(ANGLES):
                frame_x = yaw * FRAME_W_PX
                # Blender image pixels use a bottom-left origin. PNG/Godot
                # sampling uses top-left, so mirror the logical atlas row.
                frame_y = (total_rows - 1 - rows_seen) * FRAME_H_PX
                frames.append({
                    "presentation_kind": presentation,
                    "source_action": action_name,
                    "source_action_reverse_sample": reverse,
                    "frame": frame,
                    "yaw": yaw,
                    "layer_role": "body",
                    "layer_order": 0,
                    "frame_rect_px": [frame_x, frame_y, FRAME_W_PX, FRAME_H_PX],
                    "anchor_ref_cell": [FRAME_W_CELLS // 2, FRAME_H_CELLS - 1],
                    "alpha_mask_bounds_px": [frame_x, frame_y, FRAME_W_PX, FRAME_H_PX],
                    "frame_dims_cells": [FRAME_W_CELLS, FRAME_H_CELLS],
                    "source_cell_facts": source_cell_facts(frame_x, frame_y),
                })
            rows_seen += 1
    sidecar = {
        "schema": "baked_png_atlas_compositor_sidecar.v1",
        "backend": "baked_png_atlas_compositor",
        "visual_key": "demo_manny-souls",
        "source_repo": "https://github.com/catprisbrey/Cats-Godot4-Modular-Souls-like-Template",
        "source_asset": str(source_path),
        "source_asset_sha256": sha256_file(source_path),
        "source_animation_blend": str(animation_blend_path),
        "source_animation_blend_sha256": sha256_file(animation_blend_path),
        "source_weapon_asset": str(weapon_source_path),
        "source_weapon_asset_sha256": sha256_file(weapon_source_path),
        "presentation_action_map": [
            {
                "presentation_kind": presentation,
                "source_action": action_name,
                "frame_count": frame_count,
                "reverse_sample": reverse,
            }
            for presentation, action_name, frame_count, reverse in PRESENTATIONS
        ],
        "atlas_path": "res://assets/xp_sprites/demo_manny-souls.png",
        "atlas_png": "res://assets/xp_sprites/demo_manny-souls.png",
        "atlas_png_sha256": sha256_file(output_png),
        "family": "demo_souls_rig",
        "cell_scale_px": CELL_SCALE,
        "actor_pixel_size_world": 0.014,
        "render_mode": "single_source_layer",
        "rendered_layers": [0],
        "source_layer_index": 0,
        "width_cells": FRAME_W_CELLS * ANGLES,
        "height_cells": FRAME_H_CELLS * rows_seen,
        "angles": ANGLES,
        "anim_lens": [count for _, _, count, _ in PRESENTATIONS],
        "frame_w_cells": FRAME_W_CELLS,
        "frame_h_cells": FRAME_H_CELLS,
        "color_key_l0_bg": list(COLOR_KEY),
        "compositor_frames": frames,
    }
    output_json.write_text(json.dumps(sidecar, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    args = parse_args()
    source = Path(args.source).expanduser().resolve()
    animation_blend = Path(args.animation_blend).expanduser().resolve()
    weapon_source = Path(args.weapon_source).expanduser().resolve()
    output_png = Path(args.output_png).resolve()
    output_json = Path(args.output_json).resolve()
    output_png.parent.mkdir(parents=True, exist_ok=True)
    output_json.parent.mkdir(parents=True, exist_ok=True)

    load_animation_scene(animation_blend)
    import_source(source)
    sword = import_weapon_source(weapon_source)
    root = setup_root()
    setup_materials()
    setup_camera()
    setup_lighting()
    setup_render()

    temp_dir = output_png.parent / ".demo_manny_souls_bake"
    temp_dir.mkdir(parents=True, exist_ok=True)
    frame_pixels: dict[tuple[str, int, int], list[float]] = {}
    for presentation, action_name, frame_count, reverse in PRESENTATIONS:
        for frame in range(frame_count):
            for yaw in range(ANGLES):
                temp_png = temp_dir / f"{presentation}_{frame}_{yaw}.png"
                render_frame(root, sword, presentation, action_name, frame, frame_count, reverse, yaw, temp_png)
                frame_pixels[(presentation, frame, yaw)] = read_pixels(temp_png)
    write_atlas(frame_pixels, output_png)
    write_sidecar(source, animation_blend, weapon_source, output_png, output_json)
    print(f"wrote {output_png}")
    print(f"wrote {output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
