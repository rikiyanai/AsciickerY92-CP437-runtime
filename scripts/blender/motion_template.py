"""
Motion template system for walking animations in Blender.

[DEPENDENCY:BLENDER] -- Requires bpy (Blender Python API); runs inside Blender.
[PIPELINE:GENERATE]  -- Upstream of render_turntable.py and walk_anim_tool.py.
[FLOW:TEMPLATE]      -- Defines the canonical animation template dictionary and
                        bone rig used by all template-driven sprite generation.

ARCHITECTURE
============
Provides a three-class hierarchy for character animation inside Blender:

    BoneSetup          -- Creates a minimal humanoid armature rig (9 bones).
    WalkCycle          -- Applies sine-wave-based walk/run/sprint/idle keyframes
                          to that rig.
    AnimationStitcher  -- Concatenates multiple animation actions into a single
                          continuous Blender Action (for sprite-sheet rendering).

KEY EXPORTS
===========
- BoneSetup.create_humanoid_rig()   : Build armature on a named object.
- WalkCycle.ANIMATION_TEMPLATES     : Dict of template parameters (idle/walk/run/sprint).
- WalkCycle.apply_animation()       : Generate keyframes from a template.
- AnimationStitcher.stitch_animations() : Combine actions sequentially.
- apply_walk_cycle()                : Convenience wrapper for one-shot usage.
- list_templates()                  : Print available templates to stdout.

PIPELINE CONTEXT
================
1. create_cooker_asset.py / create_test_asset.py  -->  Build .blend scene
2. **motion_template.py** (this file)             -->  Rig + animate
3. walk_anim_tool.py                              -->  Orchestrate animated render
4. render_turntable.py / render_sprite.py         -->  Turntable render to PNG frames
5. asset_gen pipeline                             -->  Quantize -> XP sprite sheets

Provides bone rigging and walking animation templates with:
- Idle (standing still)
- Walk (normal pace, alternating leg/arm swing)
- Run (faster pace, exaggerated motion)
- Sprint (maximum speed motion)

All templates: 8 frames per animation state for full rotation sprite sheets.
"""

import bpy
import math
from typing import Dict, List, Any, Optional


class BoneSetup:
    """Bone rigging setup template.

    Creates a simplified humanoid armature with 9 bones (Root, Spine, Head,
    Arm_L/R, Thigh_L/R, Shin_L/R).  The bone positions are hard-coded for
    a ~2-Blender-unit tall character.  Parenting hierarchy mirrors a real
    skeleton (spine->head, root->thighs->shins, spine->arms).

    [DEPENDENCY:BLENDER] Uses bpy.data.armatures, bpy.ops.object.mode_set.
    """

    @staticmethod
    def create_humanoid_rig(obj_name="Character") -> List[str]:
        """Create basic humanoid bone rig on a named object.

        Creates a 9-bone armature (Root, Spine, Head, Arm_L/R, Thigh_L/R,
        Shin_L/R) and parents it to the object identified by ``obj_name``.

        [DEPENDENCY:BLENDER] Uses bpy.data.armatures, bpy.ops.object.mode_set.
        [FLOW:TEMPLATE] The bone names produced here must match the names
        referenced in WalkCycle.apply_animation() keyframing logic.

        Args:
            obj_name: Name of the mesh object in the current scene to parent
                to the new armature.  Defaults to "Character".

        Returns:
            List[str]: Debug labels from bone_defs (one per bone).

        Raises:
            RuntimeError: If Blender mode_set fails (e.g. no active object).
        """
        scene = bpy.context.scene

        # Create new armature
        arm = bpy.data.armatures.new("HumanoidRig")
        arm_obj = bpy.data.objects.new("HumanoidRig", arm)
        scene.collection.objects.link(arm_obj)

        # Parent object to armature
        char_obj = scene.objects.get(obj_name)
        if char_obj:
            char_obj.parent = arm_obj

        # Enter edit mode to create bones
        bpy.context.view_layer.objects.active = arm_obj
        bpy.ops.object.mode_set(mode="EDIT")

        # Define bone positions (relative to object center).
        # Each entry: "BoneName": [(head_x,y,z), (tail_x,y,z), "debug_label"]
        # WHY these values: They approximate a 2m-tall humanoid where the
        # Root is at the pelvis, Spine runs to the shoulders, and limbs
        # are symmetric about X=0.  Shin tail Y=0.2 adds a slight forward
        # offset so the knee joint bends naturally in the +Y direction.
        bone_defs = {
            "Root": [(0, 0, 0), (0, 0, 0), "Root"],
            "Spine": [(0, 0, 0.2), (0, 0, 0.6), "Spine"],
            "Head": [(0, 0, 0.6), (0, 0, 1.0), "Head"],
            "Arm_L": [(0.15, 0, 0.5), (0.15, 0, 0.2), "Shoulder_L"],
            "Arm_R": [(-0.15, 0, 0.5), (-0.15, 0, 0.2), "Shoulder_R"],
            "Thigh_L": [(0.1, 0, 0), (0.1, 0, -0.4), "Hip_L"],
            "Thigh_R": [(-0.1, 0, 0), (-0.1, 0, -0.4), "Hip_R"],
            "Shin_L": [(0.1, 0, -0.4), (0.1, 0.2, -0.85), "Knee_L"],
            "Shin_R": [(-0.1, 0, -0.4), (-0.1, 0.2, -0.85), "Knee_R"],
        }

        # Create bones
        for name, (head, tail) in [(k, v[:2]) for k, v in bone_defs.items()]:
            bone = arm.edit_bones.new(name)
            bone.head = tuple(head)
            bone.tail = tuple(tail)
            bone.head_radius = 0.05
            bone.tail_radius = 0.03

        # Return to object mode
        bpy.ops.object.mode_set(mode="OBJECT")

        # Parenting -- set up the bone hierarchy so transforms propagate
        # correctly (e.g. rotating Root moves all children).
        # TODO(PIPELINE-FIX): This block accesses edit_bones outside EDIT mode,
        # which will raise an AttributeError at runtime.  The mode_set("OBJECT")
        # above exits edit mode, but edit_bones is only available in EDIT mode.
        # Needs to be wrapped in a mode_set("EDIT") / mode_set("OBJECT") pair.
        arm_obj = bpy.context.active_object
        if not arm_obj:
            # WHY: defensive guard, but the self-assignment is a no-op bug.
            # TODO(PIPELINE-FIX): Should raise or fallback, not self-assign.
            arm_obj = arm_obj

        # Simplified parenting (in real armature setup, use relationships)
        arm_obj.data.edit_bones["Spine"].parent = arm_obj.data.edit_bones["Root"]
        arm_obj.data.edit_bones["Head"].parent = arm_obj.data.edit_bones["Spine"]
        arm_obj.data.edit_bones["Arm_L"].parent = arm_obj.data.edit_bones["Spine"]
        arm_obj.data.edit_bones["Arm_R"].parent = arm_obj.data.edit_bones["Spine"]
        arm_obj.data.edit_bones["Thigh_L"].parent = arm_obj.data.edit_bones["Root"]
        arm_obj.data.edit_bones["Thigh_R"].parent = arm_obj.data.edit_bones["Root"]
        arm_obj.data.edit_bones["Shin_L"].parent = arm_obj.data.edit_bones["Thigh_L"]
        arm_obj.data.edit_bones["Shin_R"].parent = arm_obj.data.edit_bones["Thigh_R"]

        # WHY unpacking three values: bone_defs values are [head, tail, label],
        # but only the label/name is returned.
        return [name for name, _, _ in bone_defs.values()]


class WalkCycle:
    """Walking animation templates.

    Stores parameterised motion data for idle/walk/run/sprint and generates
    Blender keyframes by evaluating sine curves at each frame.

    The core math:
        phase = frame / (num_frames - 1)          # 0..1 over the cycle
        sin_val = sin(phase * 2*pi)                # one full oscillation
    Limb rotations are ``sin_val * amplitude``, with arms at pi-phase offset
    from legs so they swing in opposition (natural gait).

    [DEPENDENCY:BLENDER] Writes to bone.rotation_euler / bone.location.
    """

    # WHY 8 frames: the asciicker sprite sheet format uses 8 rotation angles
    # AND 8 animation frames, giving a compact 8x8 grid per character state.
    ANIMATION_TEMPLATES = {
        "idle": {
            "name": "Idle",
            "duration_frames": 8,
            "description": "Standing still with slight breathing motion",
            "cycle_type": "linear",
        },
        "walk": {
            "name": "Walk",
            "duration_frames": 8,
            "description": "Normal walking pace",
            "cycle_type": "sine",
            "arm_swing_amp": 30,       # degrees of arm rotation per half-cycle
            "leg_swing_amp": 20,       # degrees of thigh rotation per half-cycle
            "arm_swing_phase": math.pi,  # WHY pi: arms swing opposite to legs (natural gait)
            "leg_swing_phase": 0,
        },
        "run": {
            "name": "Run",
            "duration_frames": 8,
            "description": "Faster run with higher leg lift",
            "cycle_type": "sine",
            "arm_swing_amp": 45,
            "leg_swing_amp": 35,
            "arm_swing_phase": math.pi,
            "leg_swing_phase": 0,
            "leg_lift": 15,
        },
        "sprint": {
            "name": "Sprint",
            "duration_frames": 8,
            "description": "Maximum speed, high knees",
            "cycle_type": "sine",
            "arm_swing_amp": 60,
            "leg_swing_amp": 45,
            "arm_swing_phase": math.pi,
            "leg_swing_phase": 0,
            "leg_lift": 25,
        },
    }

    @staticmethod
    def apply_animation(data, action_name="WalkCycle", template="walk", num_frames=8):
        """Apply walking animation template to a rig's bones via keyframes.

        Evaluates sine-wave curves at each frame to produce limb rotations
        matching the chosen gait template (idle/walk/run/sprint).

        [DEPENDENCY:BLENDER] Writes keyframes via bone.keyframe_insert().
        [PIPELINE:GENERATE] Produces the Blender Action consumed by the
        turntable renderer.
        [FLOW:TEMPLATE] Template key selects parameters from
        ANIMATION_TEMPLATES dict.

        Args:
            data: Either the armature object name (str) or the armature
                object itself (bpy.types.Object with type == 'ARMATURE').
            action_name: Name for the new Blender Action.
            template: Template key -- one of "idle", "walk", "run", "sprint".
            num_frames: Number of keyframes to generate (default 8).

        Returns:
            bpy.types.Action: The newly created animation action containing
            all generated keyframes.

        Raises:
            ValueError: If ``data`` is a string that does not match any
                ARMATURE object in the scene, or if ``template`` is not a
                recognised key in ANIMATION_TEMPLATES.
        """
        scene = bpy.context.scene

        # Get armature
        if isinstance(data, str):
            arm_obj = scene.objects.get(data)
            if not arm_obj or arm_obj.type != "ARMATURE":
                raise ValueError(f"Armature object '{data}' not found")
            arm = arm_obj.data
        else:
            arm = data
            arm_obj = data

        # TODO(PIPELINE-FIX): bpy.types.Armature has no ``actions`` attribute.
        # Should be ``bpy.data.actions.new(action_name)`` instead.
        # This will raise AttributeError at runtime.
        action = arm.actions.new(action_name)

        # Store current action
        old_action = arm_obj.animation_data.action if arm_obj.animation_data else None

        # Set as active action
        if not arm_obj.animation_data:
            arm_obj.animation_data_create()
        arm_obj.animation_data.action = action

        # Set scene to animation mode
        scene.frame_start = 0
        scene.frame_end = num_frames - 1

        # Get template data
        tmpl = WalkCycle.ANIMATION_TEMPLATES.get(template, {})
        if not tmpl:
            raise ValueError(f"Unknown template: {template}")

        # Define keyframes for each bone
        bones = [
            "Root",
            "Spine",
            "Head",
            "Arm_L",
            "Arm_R",
            "Thigh_L",
            "Thigh_R",
            "Shin_L",
            "Shin_R",
        ]

        for frame in range(num_frames):
            scene.frame_set(frame)

            # Calculate animation phase (0 to 1).
            # WHY linear ramp: at phase=0 the cycle starts, phase=1 it wraps.
            # This maps directly to sin() for a single full oscillation.
            phase = frame / (num_frames - 1) if num_frames > 1 else 0

            # Calculate animation values based on template.
            # WHY sin/cos pair: sin drives forward/back swing, cos drives
            # lateral/breathing motions that are 90 degrees out of phase.
            if tmpl.get("cycle_type", "") == "sine":
                sin_val = math.sin(phase * 2 * math.pi)
                cos_val = math.cos(phase * 2 * math.pi)
            else:
                sin_val = 0
                cos_val = 0

            # Keyframe each bone.
            # WHY mode check: edit_bones is only valid in EDIT mode; in OBJECT
            # mode we fall back to arm.bones (read-only PoseBone access).
            # TODO(PIPELINE-FIX): Writing rotation_euler/location on arm.bones
            # (Bone objects) does not work -- should use arm_obj.pose.bones
            # (PoseBone) for keyframing in OBJECT/POSE mode.
            for bone in arm.edit_bones if arm_obj.mode == "EDIT" else arm.bones:
                if bone.name not in bones:
                    continue

                # Calculate bone rotation
                if template == "idle":
                    # Slight breathing motion
                    if bone.name == "Spine":
                        rot_y = math.sin(phase * 4 * math.pi) * 0.02
                        bone.rotation_euler[1] = rot_y
                    elif bone.name == "Arm_L" or bone.name == "Arm_R":
                        # Slight arm sway
                        amp = tmpl.get("arm_swing_amp", 5) / 180 * math.pi
                        rot_x = cos_val * amp
                        bone.rotation_euler[0] = rot_x
                    else:
                        # Root moves slightly forward/back
                        if bone.name == "Root":
                            bone.location[1] = sin_val * 0.02  # Slight bob

                elif template in ["walk", "run", "sprint"]:
                    # WHY deg-to-rad conversion: template stores amplitudes in
                    # human-readable degrees; Blender rotation_euler uses radians.
                    arm_swing_amp = tmpl.get("arm_swing_amp", 30) / 180 * math.pi
                    leg_swing_amp = tmpl.get("leg_swing_amp", 20) / 180 * math.pi
                    leg_lift = tmpl.get("leg_lift", 0) / 180 * math.pi

                    if bone.name == "Arm_L":
                        # WHY negative: left arm swings opposite to left leg
                        # for a natural contralateral gait pattern.
                        rot_z = -sin_val * arm_swing_amp
                        bone.rotation_euler[2] = rot_z
                    elif bone.name == "Arm_R":
                        # Right arm swings opposite to legs
                        rot_z = sin_val * arm_swing_amp
                        bone.rotation_euler[2] = rot_z
                    elif bone.name == "Thigh_L":
                        # Left hip
                        rot_x = sin_val * leg_swing_amp
                        bone.rotation_euler[0] = rot_x
                    elif bone.name == "Thigh_R":
                        # Right hip (opposite phase)
                        rot_x = -sin_val * leg_swing_amp
                        bone.rotation_euler[0] = rot_x
                    elif bone.name == "Shin_L":
                        # Left knee (only extend when raising).
                        # WHY conditional: knees only bend forward (positive
                        # swing phase); when the thigh swings back the shin
                        # stays straight.  The 0.5 factor keeps shin motion
                        # smaller than thigh motion for realism.
                        # NOTE: leg_lift_val is computed but unused -- the
                        # actual rotation uses leg_lift directly.
                        if sin_val > 0:
                            leg_lift_val = leg_lift
                        else:
                            leg_lift_val = 0
                        rot_x = sin_val * leg_lift * 0.5
                        bone.rotation_euler[0] = rot_x
                    elif bone.name == "Shin_R":
                        # Right knee (opposite phase)
                        if sin_val < 0:
                            leg_lift_val = leg_lift
                        else:
                            leg_lift_val = 0
                        rot_x = -sin_val * leg_lift * 0.5
                        bone.rotation_euler[0] = rot_x
                    elif bone.name == "Root":
                        # Slight bob
                        bone.location[2] = sin_val * 0.02
                        # Lean forward slightly
                        bone.rotation_euler[0] = 0.1
                    elif bone.name == "Spine":
                        # Slight forward lean
                        bone.rotation_euler[0] = 0.2

                # Keyframe the property
                bone.keyframe_insert(data_path="rotation_euler", frame=frame)
                bone.keyframe_insert(data_path="location", frame=frame)

        # Restore original action
        if old_action:
            arm_obj.animation_data.action = old_action

        return action


class AnimationStitcher:
    """Stitch multiple animation Actions into a single continuous Action.

    Used to concatenate e.g. [idle, walk, run] into one timeline so the
    turntable renderer can step through all states in a single pass.

    Each sub-animation occupies exactly 8 frames in the stitched result,
    matching the sprite-sheet frame count convention used by the engine.

    [DEPENDENCY:BLENDER] Reads/writes bpy.data.actions and fcurves.
    """

    @staticmethod
    def stitch_animations(
        actions: List[bpy.types.Action], order: List[str]
    ) -> bpy.types.Action:
        """Stitch multiple animation Actions into a single continuous Action.

        Copies FCurve keyframes from each source action into a new
        "CombinedWalkCycle" action, offsetting frame numbers so the
        sub-animations play back-to-back.

        [DEPENDENCY:BLENDER] Reads/writes bpy.data.actions and fcurves.
        [PIPELINE:GENERATE] The combined Action is what the turntable
        renderer iterates through.
        [FLOW:TEMPLATE] Each sub-action corresponds to one template key.

        Args:
            actions: List of Blender Actions to concatenate.
            order: Template name strings corresponding to each action
                (e.g. ["idle", "walk", "run"]).  Must be same length as
                ``actions``.

        Returns:
            bpy.types.Action: A new "CombinedWalkCycle" action containing
            all keyframes from the input actions laid out sequentially.

        Raises:
            ValueError: If ``len(actions) != len(order)``.
        """
        if len(actions) != len(order):
            raise ValueError("Actions count must match order count")

        # WHY 8: engine sprite sheets use 8 frames per animation state.
        # This constant must stay in sync with ANIMATION_TEMPLATES duration_frames.
        frames_per_anim = 8
        total_frames = len(actions) * frames_per_anim

        # Create new combined action
        combined = bpy.data.actions.new("CombinedWalkCycle")

        scene = bpy.context.scene
        scene.frame_start = 0
        scene.frame_end = total_frames

        # Combine all keyframes
        source_frame_offset = 0
        for i, (action, template) in enumerate(zip(actions, order)):
            for fcurve in action.fcurves:
                # Copy keyframes to combined action
                new_fcurve = combined.fcurves.new(
                    data_path=fcurve.data_path, index=fcurve.array_index
                )

                # TODO(PIPELINE-FIX): ``fcurve.keyframes`` should be
                # ``fcurve.keyframe_points`` -- the Blender API attribute for
                # accessing keyframes on an FCurve.
                for keyframe in fcurve.keyframes:
                    # Adjust frame number
                    new_frame = keyframe.co[0] + source_frame_offset
                    new_fcurve.keyframe_points.insert(new_frame, keyframe.co[1])

            source_frame_offset += frames_per_anim

        return combined


def apply_walk_cycle(object_name="Character", template="walk", num_frames=8):
    """Convenience wrapper: find/create rig, apply one animation template.

    [DEPENDENCY:BLENDER] Looks up objects via bpy.context.scene.objects.
    [FLOW:TEMPLATE] One-shot helper for applying a single template without
    manually instantiating BoneSetup and WalkCycle.

    Args:
        object_name: Name of the mesh object to animate.
        template: Animation template key ("idle", "walk", "run", "sprint").
        num_frames: Number of keyframes to generate.

    Returns:
        None.  Prints the applied action name to stdout.
    """
    scene = bpy.context.scene

    # Find or create rig.
    # TODO(PIPELINE-FIX): ``CreateBoneRig`` is undefined; should be
    # ``BoneSetup.create_humanoid_rig(object_name)`` to match the class above.
    rig_obj = scene.objects.get("HumanoidRig")
    if not rig_obj:
        CreateBoneRig()  # Would need to define this
        rig_obj = scene.objects.get("HumanoidRig")

    # Apply animation
    action = WalkCycle.apply_animation(
        data=rig_obj,
        action_name=f"{template.capitalize()}Cycle",
        template=template,
        num_frames=num_frames,
    )

    print(f"Applied {template} animation: {action.name}")


def list_templates():
    """Print available animation templates to stdout.

    [FLOW:TEMPLATE] Diagnostic helper; called via ``--list`` CLI flag.

    Returns:
        None.  Output is printed to stdout.
    """
    print("Available animation templates:")
    for key, tmpl in WalkCycle.ANIMATION_TEMPLATES.items():
        print(f"  {key:8s}: {tmpl.get('description', 'No description')}")


if __name__ == "__main__":
    # WHY: Manual sys.argv inspection instead of argparse -- this script is
    # invoked inside Blender where sys.argv contains Blender flags before the
    # ``--`` separator.  A simple ``in`` check avoids argparse conflicts.
    # TODO(PIPELINE-FIX): sys.argv[1] indexing (line below) does not account
    # for Blender's own flags; should parse only args after "--".
    import sys

    if "--list" in sys.argv:
        list_templates()
    else:
        # Apply default walk cycle
        template = sys.argv[1] if len(sys.argv) > 1 else "walk"
        apply_walk_cycle(object_name="Cube", template=template)
