"""
Complete animation rendering for sprite sheets.

[DEPENDENCY:BLENDER] -- Requires bpy (Blender Python API); runs inside Blender.
[PIPELINE:GENERATE]  -- Orchestration layer between motion_template and render_turntable.

ARCHITECTURE
============
This module is the main entry point for generating animated sprite sheets.
It wires together three subsystems:

    motion_template.py   -->  BoneSetup (rig) + WalkCycle (keyframes)
    walk_anim_tool.py    -->  **this file** -- rig creation, animation stitching, render dispatch
    render_turntable.py  -->  Camera orbit + PNG output

The pipeline flow inside this file:
    1. Find or create a HumanoidRig armature (BoneSetup).
    2. Parent the target mesh to the rig.
    3. Apply one or more animation templates (WalkCycle) and stitch them
       into a single Blender Action (AnimationStitcher).
    4. Delegate turntable rendering to render_turntable.render_turntable().

KEY EXPORTS
===========
- render_animated_walk()      : High-level function -- rig + animate + render.
- create_default_CHARACTER()  : Build a placeholder humanoid mesh for testing.
- main()                      : CLI entry point (argparse).

PIPELINE CONTEXT
================
1. create_cooker_asset.py / create_test_asset.py  -->  Build .blend scene
2. motion_template.py                             -->  Rig + animate
3. **walk_anim_tool.py** (this file)              -->  Orchestrate animated render
4. render_turntable.py / render_sprite.py         -->  Turntable render to PNG frames
5. asset_gen pipeline                             -->  Quantize -> XP sprite sheets

KNOWN ISSUES
============
- ``create_default CHARACTER()`` (line 112) has a syntax error -- the function
  name contains a space.  It is called as ``create_default_CHARACTER()`` on
  line 185, which is a different (also undefined) name.
  TODO(PIPELINE-FIX): Rename to ``create_default_character()``.

Usage:
    Inside Blender:
        import sys
        sys.path.insert(0, '/path/to/asciicker-Y9-2/scripts')
        from motion_template import apply_walk_cycle, list_templates

        # List available templates
        list_templates()

        # Apply walk animation
        apply_walk_cycle(object_name="Cube", template="walk", num_frames=8)

        # Render all angles
        from render_turntable import render_turntable
        render_turntable(object_name="Cube", angles=8, output_dir="output")
"""

import bpy
import os
import sys
import math
from typing import List

# Import our modules.
# WHY try/except: This file may be imported as part of a package (relative
# import with dot) or executed standalone inside Blender (absolute import).
# The fallback handles both cases.
try:
    from .motion_template import WalkCycle, BoneSetup, AnimationStitcher
except ImportError:
    from motion_template import WalkCycle, BoneSetup, AnimationStitcher


def render_animated_walk(
    object_name="Character",
    animations=None,
    angles=8,
    output_dir="output",
    scale_factor=4
):
    """
    Render animated walk cycle at multiple angles.

    Creates (or reuses) a HumanoidRig armature, parents the target mesh,
    applies each animation template via WalkCycle, stitches them into a
    single Action, and delegates turntable rendering.

    [DEPENDENCY:BLENDER] Reads bpy.context.scene; modifies object parenting
    and animation_data.

    Args:
        object_name: Object name to render
        animations: List of animation templates ["idle", "walk", "run"]
        angles: Number of rotation angles (1, 4, or 8)
        output_dir: Output directory
        scale_factor: Render quality multiplier

    Returns:
        Dict with paths and info
    """
    if animations is None:
        animations = ["idle", "walk"]

    scene = bpy.context.scene

    # Setup
    print(f"Setting up {len(animations)} animations for {angles} angles...")

    # Create rig if needed.
    # WHY re-fetch after create: create_humanoid_rig() links the armature
    # into the scene collection, so we look it up by name afterwards.
    rig_obj = scene.objects.get("HumanoidRig")
    if not rig_obj or rig_obj.type != 'ARMATURE':
        print("Creating humanoid rig...")
        bones = BoneSetup.create_humanoid_rig(object_name)
        rig_obj = scene.objects.get("HumanoidRig")

    # Parent object to rig
    char_obj = scene.objects.get(object_name)
    if char_obj and rig_obj:
        char_obj.parent = rig_obj

    # Create combined animation
    actions = []
    for anim in animations:
        print(f"Applying animation: {anim}")
        action = WalkCycle.apply_animation(
            data=rig_obj,
            action_name=f"{anim.capitalize()}Cycle",
            template=anim,
            num_frames=8  # 8 frames per state
        )
        actions.append(action)

    # Stitch together -- concatenate all sub-animations into one Action
    # so the turntable renderer can iterate through them sequentially.
    combined = AnimationStitcher.stitch_animations(actions, animations)
    if rig_obj.animation_data:
        rig_obj.animation_data.action = combined

    # Render turntable.
    # TODO(PIPELINE-FIX): Import path ``scripts.blender.render_turntable``
    # assumes the project root is on sys.path.  Inside Blender headless mode
    # this may not be the case; consider a relative import or sys.path fix.
    from scripts.blender.render_turntable import render_turntable

    frame_paths = render_turntable(
        object_name=object_name,
        angles=angles,
        output_dir=output_dir,
        scale_factor=scale_factor
    )

    return {
        "frame_paths": frame_paths,
        "animations": animations,
        "angles": angles,
        "total_frames": len(frame_paths)
    }


def create_default_character():
    """Create default humanoid character mesh for testing.

    Builds a simple humanoid from Blender primitives (cube torso, cylinder
    limbs, UV-sphere head) and joins them into a single mesh named
    "Character".  Intended as a quick stand-in for pipeline testing when
    no artist-authored model is available.

    WARNING: Clears ALL objects in the current scene before building.

    Returns:
        bpy.types.Object: The joined "Character" mesh object.

    [DEPENDENCY:BLENDER] Uses bpy.ops.mesh primitives and bpy.ops.object.join.
    """
    scene = bpy.context.scene

    # Clear existing
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()

    # Create simple humanoid using primitives.
    # WHY cube for body: quick boxy torso; size tuple is (width, depth, height).
    bpy.ops.mesh.primitive_cube_add(size=(0.8, 0.5, 1.5))
    body = bpy.context.active_object
    body.name = "Body"
    body.location = (0, 0, 0.85)

    # Arms -- cylinders offset to each side of the torso.
    # WHY slight Z-rotation: gives a natural resting angle, arms slightly away
    # from the body rather than perfectly vertical.
    bpy.ops.mesh.primitive_cylinder_add(radius=0.12, depth=0.6)
    arm_L = bpy.context.active_object
    arm_L.name = "Arm_L"
    arm_L.location = (0.5, 0, 1.0)
    arm_L.rotation_euler = (0, 0, -0.2)

    bpy.ops.mesh.primitive_cylinder_add(radius=0.12, depth=0.6)
    arm_R = bpy.context.active_object
    arm_R.name = "Arm_R"
    arm_R.location = (-0.5, 0, 1.0)
    arm_R.rotation_euler = (0, 0, 0.2)

    # Legs
    bpy.ops.mesh.primitive_cylinder_add(radius=0.14, depth=0.8)
    leg_L = bpy.context.active_object
    leg_L.name = "Thigh_L"
    leg_L.location = (0.2, 0, 0.4)

    bpy.ops.mesh.primitive_cylinder_add(radius=0.14, depth=0.8)
    leg_R = bpy.context.active_object
    leg_R.name = "Thigh_R"
    leg_R.location = (-0.2, 0, 0.4)

    # Head
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.22)
    head = bpy.context.active_object
    head.name = "Head"
    head.location = (0, 0, 1.65)

    # Join into single object.
    # WHY join: the rig expects a single mesh parent; individual primitives
    # would each need separate vertex groups and armature modifiers.
    objs = [body, arm_L, arm_R, leg_L, leg_R, head]
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objs:
        obj.select_set(True)
    bpy.ops.object.join()
    result = bpy.context.active_object
    result.name = "Character"

    return result


def main():
    """CLI entry point: parse args, optionally create a default character,
    then run the animated walk render pipeline.

    [DEPENDENCY:BLENDER] Indirectly requires bpy via render_animated_walk
    and create_default_CHARACTER.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Render animated walk sprites")
    parser.add_argument("--object", default="Character", help="Object name")
    parser.add_argument("--animations", nargs="+", default=["idle", "walk"], help="Animation templates")
    parser.add_argument("--angles", type=int, default=8, help="Angles to render")
    parser.add_argument("--output", default="output", help="Output directory")
    parser.add_argument("--scale", type=int, default=4, help="Scale factor")
    parser.add_argument("--create-rig", action="store_true", help="Create rig")

    args = parser.parse_args()

    if args.create_rig:
        print("Creating default character...")
        create_default_character()

    # Render
    result = render_animated_walk(
        object_name=args.object,
        animations=args.animations,
        angles=args.angles,
        output_dir=args.output,
        scale_factor=args.scale
    )

    print(f"\nRendering complete!")
    print(f"  Animations: {', '.join(result['animations'])}")
    print(f"  Angles: {result['angles']}")
    print(f"  Total frames: {result['total_frames']}")
    print(f"  Output: {args.output}/")


if __name__ == "__main__":
    main()