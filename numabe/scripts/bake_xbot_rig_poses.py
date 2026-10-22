"""FL-4407 / FL-4907: compile the operator's Mixamo X Bot downloads with Blender.

Run Blender --background --factory-startup --python-exit-code 1 --python this_file -- --source-dir DIR.

FL-4907 XBOT_52_BONE_OCTAHEDRON_RIG_20261002 (operator order 2026-10-02): the
human actor is the Mixamo Standard Rig (Full Fingers): 52 deforming bones,
416 triangles, weapons excluded. This compiler emits three products:

* assets/world_mesh/xbot_rig_rigid_mesh.json (true-ff-rigid-mesh.v1): one
  octahedron (8 triangles, degenerate-quad rows) per deforming bone, in that
  bone's rest frame. Part index = deforming-bone index. The octahedron spans
  the bone from its head to its child joint (or *_End marker), extended where
  the bone's skin-weighted source vertices reach past the joints; its cross
  section is the 2nd..98th percentile extent of those vertices.
* assets/actor_mesh/xbot_rig_bone_transforms.bin: per sampled frame, one
  float32 row-major 3x4 transform per part (rest frame -> actor space), the
  RigidMoverGeometry pose-row layout.
* assets/actor_mesh/xbot_rig_poses.json: clips, aliases, aim clips, markers,
  weapon attachment and per-pose transform offsets and bounds.

Skin measurement happens only here. Runtime never receives a posed face row:
it retains the 416-triangle mesh once and writes 52 part transforms per actor.
"""
import argparse
import hashlib
import json
import math
import re
import struct
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector, Matrix, Euler

CLIPS = {
    "idle": ("Standard Idle.fbx", True),
    "walk": ("Female Basic Locomotion Pack/walking.fbx", True),
    "run": ("Female Basic Locomotion Pack/running.fbx", True),
    "walk_left": ("Female Basic Locomotion Pack/left strafe walk.fbx", True),
    "walk_right": ("Female Basic Locomotion Pack/right strafe walk.fbx", True),
    "run_left": ("Female Basic Locomotion Pack/left strafe.fbx", True),
    "run_right": ("Female Basic Locomotion Pack/right strafe.fbx", True),
    "walk_back": ("Pro Longbow Pack/standing walk back.fbx", True),
    "run_back": ("Pro Longbow Pack/standing run back.fbx", True),
    # Held stance is a server action, not a shortened standing mesh. Compile
    # the operator's authored crouch into the same immutable rig bank so the
    # action selector can keep the body present while crouch is held.
    "crouch": ("Pro Sword and Shield Pack/sword and shield crouch idle.fbx", True),
    "jump": ("Female Basic Locomotion Pack/jump.fbx", False),
    # FL-4407: authored Mixamo sword cuts replace the frozen "Standard Idle"
    # placeholders. Selected on measured sword-hand vertical-to-horizontal
    # travel so the cut is a real arc, not a ground-parallel sweep.
    "melee_cut_1": ("Pro Sword and Shield Pack/sword and shield attack (4).fbx", False),
    "melee_cut_2": ("Pro Sword and Shield Pack/sword and shield slash.fbx", False),
    "melee_cut_3": ("Pro Sword and Shield Pack/sword and shield slash (4).fbx", False),
    # FL-4806: an equipped sword owns its own stance. The sword pack's idle,
    # walk and run replace the unarmed gait while weapon_sword is equipped, so
    # the blade hand is closed and carried instead of swinging open.
    "sword_idle": ("Pro Sword and Shield Pack/sword and shield idle.fbx", True),
    "sword_walk": ("Pro Sword and Shield Pack/sword and shield walk.fbx", True),
    "sword_run": ("Pro Sword and Shield Pack/sword and shield run.fbx", True),
    "longbow_shot": ("Pro Longbow Pack/standing aim overdraw.fbx", False),
    "death": ("Standing Death Left 01.fbx", False),
    "hurt": ("Standard Idle.fbx", False),
    "block": ("Pro Longbow Pack/standing block.fbx", True),
    # FL-4407: no downloaded pack contains a seated clip, so the ridden pose is
    # authored offline from the standing idle. The idle's upper-body sway is
    # kept so a rider reads as alive; only the legs are overridden.
    "mounted_idle": ("Standard Idle.fbx", True),
}
ALIASES = {"idle": "idle", "move": "run", "walk": "walk", "run": "run",
           "jump": "jump", "dash": "run", "attack": "melee_cut_1", "dash_attack": "melee_cut_3",
           "hurt": "hurt", "guard_break": "hurt", "block": "block", "deflected": "block",
           "dead": "death", "deathblow": "death"}
ALIASES.update({key: key for key in CLIPS if key.startswith(("walk_", "run_"))})
# Longbow locomotion retains the source legs and newly authors the arm layer
# offline. No runtime skinning or per-frame mesh construction is introduced.
# FL-4806: the longbow legs come from the operator's Longbow Locomotion Pack,
# which is authored for a bow stance. The unarmed pack remains the fallback
# for a gait the bow pack does not carry.
LONGBOW_LOCOMOTION = {
    "idle": "standing idle 01.fbx", "walk": "standing walk forward.fbx",
    "run": "standing run forward.fbx", "walk_left": "standing walk left.fbx",
    "walk_right": "standing walk right.fbx", "run_left": "standing run left.fbx",
    "run_right": "standing run right.fbx", "walk_back": "standing walk back.fbx",
    "run_back": "standing run back.fbx",
}
for key in list(CLIPS):
    if key == "idle" or key.startswith(("walk", "run")):
        CLIPS["longbow_" + key] = (("Longbow Locomotion Pack/" + LONGBOW_LOCOMOTION[key], CLIPS[key][1])
                                   if key in LONGBOW_LOCOMOTION else CLIPS[key])
ALIASES.update({key: key for key in CLIPS})
BASE = "Pro Longbow Pack/X Bot.fbx"
FPS = 15
PROJECT = Path(__file__).resolve().parents[1] / "godot_project"
# FL-4907 XBOT_52_BONE_OCTAHEDRON_RIG_20261002: the 65-bone Mixamo skeleton
# carries 13 leaf markers (HeadTop_End, Toe_End x2, finger 4 x10) that hold no
# skin weight. The other 52 bones are the Standard Rig (Full Fingers) parts.
LEAF_MARKER = re.compile(r"(_End|Hand(?:Thumb|Index|Middle|Ring|Pinky)4)$")
SOURCE_BONES = 65
DEFORMING_BONES = 52
TRIANGLES_PER_PART = 8
MESH_KEY = "xbot_rig_v1"
MESH_SCHEMA = "true-ff-rigid-mesh.v1"
MESH_RESOURCE = "res://assets/world_mesh/xbot_rig_rigid_mesh.json"
TRANSFORM_RESOURCE = "res://assets/actor_mesh/xbot_rig_bone_transforms.bin"
PRODUCT_SCHEMA = "asciicker.actor_mesh.rig_bone_poses.v1"
TRANSFORM_FLOATS = 12
MATERIAL = 237.0
SHADE = 1.0
GRID = 4096.0
# Robust skin extent: the 2nd..98th percentile of a bone's dominant vertices.
SKIN_PERCENTILE = 2.0
MIN_SKIN_VERTICES = 8
MIN_RADIUS = 0.006
# Equator stays inside the middle 60% of the axial span (no flat cap face).
EQUATOR_MARGIN = 0.2
SILHOUETTE_CELL = 0.02


def sha(data):
    return hashlib.sha256(data).hexdigest()


def import_fbx(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=str(path))
    objects = set(bpy.data.objects) - before
    arms = [o for o in objects if o.type == "ARMATURE"]
    if len(arms) != 1:
        raise RuntimeError(f"Expected one armature: {path}")
    return arms[0], sorted((o for o in objects if o.type == "MESH"), key=lambda o: o.name), objects


def retarget_pose(target, source):
    """Transfer world-space rest-relative rotations through target bone lengths."""
    desired = {}
    target_rotation_inverse = target.matrix_world.to_quaternion().inverted()
    for bone in target.data.bones:
        src_rest = source.matrix_world @ source.data.bones[bone.name].matrix_local
        src_pose = source.matrix_world @ source.pose.bones[bone.name].matrix
        dst_rest = target.matrix_world @ bone.matrix_local
        delta = src_pose.to_quaternion() @ src_rest.to_quaternion().inverted()
        rotation = target_rotation_inverse @ delta @ dst_rest.to_quaternion()
        matrix = rotation.to_matrix().to_4x4()
        if bone.parent:
            parent_matrix = desired[bone.parent.name]
            offset = bone.parent.matrix_local.inverted() @ bone.matrix_local.translation
            matrix.translation = parent_matrix @ offset
            local_rest = bone.parent.matrix_local.inverted() @ bone.matrix_local
            local_pose = parent_matrix.inverted() @ matrix
        else:
            shift = target.matrix_world.inverted().to_3x3() @ (src_pose.translation - src_rest.translation)
            matrix.translation = bone.matrix_local.translation + shift
            local_rest, local_pose = bone.matrix_local, matrix
        desired[bone.name] = matrix
        target.pose.bones[bone.name].matrix_basis = local_rest.inverted() @ local_pose
    bpy.context.view_layer.update()


def authored_weapon_pose(clip, phase, authoring, pitch=0):
    """Authored grip path, blade sweep and longbow draw, in actor-local units."""
    if clip.startswith("melee_cut"):
        reverse = -1 if clip.endswith("2") else 1
        # Anticipation -> committed cut -> follow-through -> recover.
        keys = [(0, .15), (.3, 1.15), (.55, -1.15), (.7, -1.35), (1, .15)]
        angle = keys[-1][1]
        for (a,x),(b,y) in zip(keys,keys[1:]):
            if phase <= b:
                t=max(0,min(1,(phase-a)/(b-a)));t=t*t*(3-2*t)
                angle=x+(y-x)*t;break
        angle *= -reverse
        down = .55 if clip.endswith("3") else .12
        grip = Vector((math.sin(angle)*.30, 1.18 + math.cos(angle)*.12, .12+math.cos(angle)*.26))
        basis = Euler((down, angle, 0), 'YXZ').to_matrix()
        return grip, basis, Vector((.24,1.03,.15))
    grip=Vector(authoring['grip_actor'])
    support=grip+Vector((.24,.10,-.02))
    if clip=="longbow_shot":
        # Left hand holds the bow. Right hand draws to the cheek, holds, then
        # releases before returning to the ready position.
        ready=Vector((.24,.10,-.02))
        full=Vector((.30,.22,float(authoring['full_draw_nock_z'])))
        released=Vector((.22,.18,.04))
        keys=[(0,ready),(.58,full),(.72,full),(.76,released),(1,ready)]
        for (a,x),(b,y) in zip(keys,keys[1:]):
            if phase<=b:
                t=max(0,min(1,(phase-a)/(b-a)));t=t*t*(3-2*t);support=grip+x.lerp(y,t);break
    basis=Euler((-math.radians(pitch),0,0)).to_matrix()
    pivot=Vector(authoring['aim_pivot_actor'])
    return pivot+basis@(grip-pivot), basis, pivot+basis@(support-pivot)


def install_authored_arms(arm):
    targets={}
    for side in ("Right","Left"):
        target=bpy.data.objects.new("Authored "+side+" wrist",None)
        bpy.context.scene.collection.objects.link(target)
        ik=arm.pose.bones["mixamorig:"+side+"ForeArm"].constraints.new('IK')
        ik.target=target;ik.chain_count=2;ik.use_stretch=False
        targets[side]=target
    return targets


def actor_point(point, shift, scale, ground):
    """Blender Z-up world point -> Godot Y-up actor point. X Bot faces Blender -Y."""
    p = point - shift
    return Vector((p.x * scale, (p.z - ground) * scale, -p.y * scale))


def actor_direction(vector):
    return Vector((vector.x, vector.z, -vector.y))


def actor_frame(arm, armature_matrix, shift, scale, ground):
    """Rigid actor-space frame of one bone matrix: orthonormal basis, origin.

    The axis change is a proper rotation and the armature scale is uniform, so
    the bone frame stays rigid; Gram-Schmidt only removes float drift."""
    matrix = arm.matrix_world @ armature_matrix
    linear = matrix.to_3x3()
    x = actor_direction(linear.col[0]).normalized()
    y = actor_direction(linear.col[1])
    y = (y - x * x.dot(y)).normalized()
    z = x.cross(y)
    if z.dot(actor_direction(linear.col[2])) <= 0.0:
        raise RuntimeError("Bone frame is not a proper rotation")
    return Matrix((x, y, z)).transposed(), actor_point(matrix.translation, shift, scale, ground)


def transform_row(basis, origin):
    """RigidMoverGeometry pose-row layout: row-major 3x4 [basis | origin]."""
    return [basis[0][0], basis[0][1], basis[0][2], origin.x,
            basis[1][0], basis[1][1], basis[1][2], origin.y,
            basis[2][0], basis[2][1], basis[2][2], origin.z]


def snap(value):
    return round(float(value) * GRID) / GRID


def deforming_bones(arm):
    names = [bone.name for bone in arm.data.bones]
    deforming = [name for name in names if not LEAF_MARKER.search(name)]
    if len(names) != SOURCE_BONES or len(deforming) != DEFORMING_BONES:
        raise RuntimeError(f"X Bot skeleton is not the 52-bone Standard Rig: {len(names)} bones, "
                           f"{len(deforming)} deforming")
    return names, deforming


# Vertex 0 bottom apex, 1 top apex, 2..5 equator (+x, +z, -x, -z). Each face
# is wound so (b-a)x(c-a) points out of the solid; the first triangle of every
# row is the face itself, never degenerate.
OCTAHEDRON_FACES = [face for index in range(4)
                    for face in ((1, 2 + (index + 1) % 4, 2 + index), (0, 2 + index, 2 + (index + 1) % 4))]


def octahedron(center_x, center_z, y0, y1, y_equator, radius_x, radius_z):
    """Six grid-snapped vertices of one part, in its bone's rest frame."""
    bottom = (center_x, y0, center_z)
    top = (center_x, y1, center_z)
    ring = [(center_x + radius_x, y_equator, center_z), (center_x, y_equator, center_z + radius_z),
            (center_x - radius_x, y_equator, center_z), (center_x, y_equator, center_z - radius_z)]
    return [tuple(snap(v) for v in point) for point in [bottom, top, *ring]]


def measure_rig(arm, meshes, scale, ground):
    """One octahedron per deforming bone, sized from its skin-weighted vertices.

    Each source vertex belongs to the deforming bone with its largest skin
    weight (ties: lowest bone index). Its rest position, in that bone's rest
    frame, measures the bone's axial reach and cross section."""
    names, deforming = deforming_bones(arm)
    index = {name: part for part, name in enumerate(deforming)}
    zero = Vector((0.0, 0.0, 0.0))
    frames = {name: actor_frame(arm, arm.data.bones[name].matrix_local, zero, scale, ground)
              for name in names}
    samples = {name: [] for name in deforming}
    for mesh in meshes:
        groups = {group.index: group.name for group in mesh.vertex_groups}
        for vertex in mesh.data.vertices:
            best = None
            for group in vertex.groups:
                name = groups.get(group.group, "")
                if group.weight <= 0.0:
                    continue
                if LEAF_MARKER.search(name):
                    raise RuntimeError(f"Leaf marker carries skin weight: {name}")
                if name not in index:
                    continue
                key = (round(group.weight, 6), -index[name])
                if best is None or key > best[0]:
                    best = (key, name)
            if best is None:
                raise RuntimeError(f"Vertex without deforming-bone weight in {mesh.name}")
            basis, origin = frames[best[1]]
            point = actor_point(mesh.matrix_world @ vertex.co, zero, scale, ground)
            samples[best[1]].append(tuple(basis.transposed() @ (point - origin)))
    rows, parts, octahedra, bones = [], [], [], []
    for part, name in enumerate(deforming):
        basis, origin = frames[name]
        children = [child.name for child in arm.data.bones[name].children]
        if not children:
            raise RuntimeError(f"Deforming bone has no child joint: {name}")
        reach = {child: (basis.transposed() @ (frames[child][1] - origin)).y for child in children}
        child = max(children, key=lambda child_name: (reach[child_name], child_name))
        length = max(reach[child], 0.01)
        points = np.array(samples[name], dtype=np.float64).reshape(-1, 3)
        fallback = len(points) < MIN_SKIN_VERTICES
        if fallback:
            low = np.array([-0.18 * length, 0.0, -0.18 * length])
            high = np.array([0.18 * length, length, 0.18 * length])
            median_y = 0.5 * length
        else:
            low = np.percentile(points, SKIN_PERCENTILE, axis=0)
            high = np.percentile(points, 100.0 - SKIN_PERCENTILE, axis=0)
            median_y = float(np.median(points[:, 1]))
        y0 = min(0.0, float(low[1]))
        y1 = max(length, float(high[1]))
        span = y1 - y0
        y_equator = min(max(median_y, y0 + EQUATOR_MARGIN * span), y1 - EQUATOR_MARGIN * span)
        center_x = 0.5 * float(low[0] + high[0])
        center_z = 0.5 * float(low[2] + high[2])
        radius_x = max(0.5 * float(high[0] - low[0]), MIN_RADIUS)
        radius_z = max(0.5 * float(high[2] - low[2]), MIN_RADIUS)
        vertices = octahedron(center_x, center_z, y0, y1, y_equator, radius_x, radius_z)
        for a, b, c in OCTAHEDRON_FACES:
            pa, pb, pc = vertices[a], vertices[b], vertices[c]
            if (Vector(pb) - Vector(pa)).cross(Vector(pc) - Vector(pa)).length <= 1e-9:
                raise RuntimeError(f"Degenerate octahedron face: {name}")
            rows.extend((*pa, MATERIAL, *pb, SHADE, *pc, float(part), *pc, 0.0))
            parts.append(part)
        octahedra.append(vertices)
        rig_extent = [2.0 * radius_x, span, 2.0 * radius_z]
        skin_extent = [float(v) for v in (high - low)]
        bones.append({"part": part, "bone": name, "child_joint": child,
                      "skin_vertices": int(len(points)), "fallback_radius": fallback,
                      "length": round(length, 6), "span": [round(y0, 6), round(y1, 6)],
                      "skin_extent": [round(v, 6) for v in skin_extent],
                      "rig_extent": [round(v, 6) for v in rig_extent],
                      "extent_ratio": [round(r / s, 4) if s > 1e-6 else None
                                       for r, s in zip(rig_extent, skin_extent)]})
    if len(rows) != DEFORMING_BONES * TRIANGLES_PER_PART * 16:
        raise RuntimeError("Octahedron rig is not 416 triangles")
    return {"names": names, "deforming": deforming, "rows": rows, "parts": parts,
            "octahedra": octahedra, "bones": bones}


def mesh_triangles(meshes, depsgraph, shift, scale, ground, evaluated):
    """Actor-space source triangles (n, 3, 3) of the rest or evaluated meshes."""
    out = []
    for mesh in meshes:
        holder = mesh.evaluated_get(depsgraph) if evaluated else None
        geometry = holder.to_mesh() if evaluated else mesh.data
        geometry.calc_loop_triangles()
        coordinates = np.empty(len(geometry.vertices) * 3)
        geometry.vertices.foreach_get("co", coordinates)
        triangles = np.empty(len(geometry.loop_triangles) * 3, dtype=np.int64)
        geometry.loop_triangles.foreach_get("vertices", triangles)
        world_matrix = np.array(mesh.matrix_world)
        world = coordinates.reshape(-1, 3) @ world_matrix[:3, :3].T + world_matrix[:3, 3]
        world = world - np.array(shift)
        actor = np.stack([world[:, 0] * scale, (world[:, 2] - ground) * scale, -world[:, 1] * scale], axis=1)
        out.append(actor[triangles.reshape(-1, 3)])
        if evaluated:
            holder.to_mesh_clear()
    return np.concatenate(out)


def rig_triangles(rig, frames):
    out = []
    for part, vertices in enumerate(rig["octahedra"]):
        basis, origin = frames[part]
        posed = [basis @ Vector(v) + origin for v in vertices]
        for a, b, c in OCTAHEDRON_FACES:
            out.append([tuple(posed[a]), tuple(posed[b]), tuple(posed[c])])
    return np.array(out, dtype=np.float64)


def raster(triangles, low, shape):
    """Cell-centre coverage of 2D triangles, plus every vertex cell."""
    grid = np.zeros(shape, dtype=bool)
    cells = np.floor((triangles.reshape(-1, 2) - low) / SILHOUETTE_CELL).astype(np.int64)
    cells = np.clip(cells, 0, np.array(shape) - 1)
    grid[cells[:, 0], cells[:, 1]] = True
    for triangle in triangles:
        first = np.maximum(np.floor((triangle.min(axis=0) - low) / SILHOUETTE_CELL).astype(np.int64), 0)
        last = np.minimum(np.floor((triangle.max(axis=0) - low) / SILHOUETTE_CELL).astype(np.int64),
                          np.array(shape) - 1)
        if first[0] == last[0] and first[1] == last[1]:
            continue
        xs = low[0] + (np.arange(first[0], last[0] + 1) + 0.5) * SILHOUETTE_CELL
        ys = low[1] + (np.arange(first[1], last[1] + 1) + 0.5) * SILHOUETTE_CELL
        gx, gy = np.meshgrid(xs, ys, indexing="ij")
        (x0, y0), (x1, y1), (x2, y2) = triangle
        determinant = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(determinant) < 1e-12:
            continue
        l0 = ((y1 - y2) * (gx - x2) + (x2 - x1) * (gy - y2)) / determinant
        l1 = ((y2 - y0) * (gx - x2) + (x0 - x2) * (gy - y2)) / determinant
        inside = (l0 >= 0.0) & (l1 >= 0.0) & (l0 + l1 <= 1.0)
        grid[first[0]:last[0] + 1, first[1]:last[1] + 1] |= inside
    return grid


def silhouette_parity(source, rig):
    """Front (x,y) and side (z,y) silhouette IoU and area ratio, rig vs source."""
    result = {}
    for view, axes in (("front", [0, 1]), ("side", [2, 1])):
        a, b = source[:, :, axes], rig[:, :, axes]
        points = np.concatenate([a.reshape(-1, 2), b.reshape(-1, 2)])
        low = points.min(axis=0) - SILHOUETTE_CELL
        shape = tuple((np.ceil((points.max(axis=0) + SILHOUETTE_CELL - low) / SILHOUETTE_CELL)).astype(int) + 1)
        source_cells, rig_cells = raster(a, low, shape), raster(b, low, shape)
        union = int((source_cells | rig_cells).sum())
        result[view] = {"iou": round(int((source_cells & rig_cells).sum()) / union, 4),
                        "area_ratio": round(int(rig_cells.sum()) / int(source_cells.sum()), 4)}
    return result


def compile_product(source, output):
    weapon_path=Path(__file__).resolve().parents[1]/'godot_project/assets/actor_mesh/longbow_weapon.json'
    weapon_bytes=weapon_path.read_bytes()
    authoring=json.loads(weapon_bytes)['coordinate_contract']
    aim_clips={}
    compiled_clips=dict(CLIPS)
    for key in CLIPS:
        if not key.startswith('longbow_'):continue
        variants=[]
        for pitch in authoring['aim_pitch_degrees']:
            variant=key if pitch==0 else key+'__pitch_'+str(pitch)
            compiled_clips[variant]=CLIPS[key]
            variants.append(variant)
        aim_clips[key]={'degrees':authoring['aim_pitch_degrees'],'clips':variants}
    transform_blob = bytearray()
    poses, clips, sources = [], {}, {}
    original_triangles = 0
    rig = None
    rig_scale = rig_ground = None
    parity = {}
    for clip_id, (relative, loop) in compiled_clips.items():
        base_clip=clip_id.split('__pitch_')[0]
        pitch=int(clip_id.split('__pitch_')[1]) if '__pitch_' in clip_id else 0
        bpy.ops.wm.read_factory_settings(use_empty=True)
        animated, animated_meshes, imported = import_fbx(source / relative)
        action = animated.animation_data.action
        slot = animated.animation_data.action_slot
        source_fps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
        first, last = action.frame_range
        duration = (last - first) / source_fps
        # FL-4407: an authored action freezes one source frame and drives the
        # arms from the offline weapon path. A combat action plays its own
        # source animation and lets the animated hand carry the weapon.
        authored_action = base_clip in ("longbow_shot", "hurt")
        combat_action = base_clip.startswith("melee_cut")
        if authored_action: duration = 1.0
        gait_id = base_clip.removeprefix("longbow_").removeprefix("sword_") if base_clip != "longbow_shot" else base_clip
        base_relative = BASE
        arm, meshes, _ = import_fbx(source / base_relative)
        if [b.name for b in animated.data.bones] != [b.name for b in arm.data.bones]:
            raise RuntimeError(f'Bone map mismatch: {relative}')
        rest_error = max(max(abs(a - b) for ra, rb in zip(x.matrix_local, y.matrix_local)
                             for a, b in zip(ra, rb))
                         for x, y in zip(arm.data.bones, animated.data.bones))
        retarget = rest_error > 0.001
        if retarget:
            arm.animation_data_clear()
            for obj in imported:
                if obj != animated:
                    bpy.data.objects.remove(obj, do_unlink=True)
        else:
            arm.animation_data_create()
            arm.animation_data.action = action
            arm.animation_data.action_slot = slot
            for obj in imported:
                bpy.data.objects.remove(obj, do_unlink=True)
        sources[base_relative] = sha((source / base_relative).read_bytes())
        if len(arm.data.bones) != SOURCE_BONES or not meshes:
            raise RuntimeError('X Bot source skeleton/mesh identity mismatch')
        source_triangle_total = 0
        for mesh in meshes:
            mesh.data.calc_loop_triangles()
            source_triangle_total += len(mesh.data.loop_triangles)
        original_triangles = max(original_triangles, source_triangle_total)
        # One X Bot mesh and neutral height for every clip. Retargeting handles
        # pack bind differences; never ground each animation frame separately.
        neutral = [m.matrix_world @ v.co for m in meshes for v in m.data.vertices]
        ground = min(v.z for v in neutral)
        height = max(v.z for v in neutral) - ground
        scale = 1.8 / height
        depsgraph = bpy.context.evaluated_depsgraph_get()
        if rig is None:
            # The rig is measured once, in the bind pose, in actor units. Every
            # clip re-imports the same base, so its scale and ground must agree.
            rig_scale, rig_ground = scale, ground
            rig = measure_rig(arm, meshes, scale, ground)
            zero = Vector((0.0, 0.0, 0.0))
            rest_frames = [actor_frame(arm, arm.data.bones[name].matrix_local, zero, scale, ground)
                           for name in rig["deforming"]]
            parity["bind"] = silhouette_parity(
                mesh_triangles(meshes, depsgraph, zero, scale, ground, False), rig_triangles(rig, rest_frames))
            print(f"XBOT_RIG parts={len(rig['deforming'])} triangles={len(rig['parts'])} "
                  f"source_triangles={source_triangle_total} bind_parity={json.dumps(parity['bind'])}", flush=True)
            for bone in rig["bones"]:
                print("XBOT_BONE_EXTENT part=%02d bone=%s child=%s skin_vertices=%d skin=%s rig=%s ratio=%s%s" % (
                    bone["part"], bone["bone"], bone["child_joint"], bone["skin_vertices"],
                    bone["skin_extent"], bone["rig_extent"], bone["extent_ratio"],
                    " fallback_radius" if bone["fallback_radius"] else ""), flush=True)
        elif abs(scale - rig_scale) > 1e-9 or abs(ground - rig_ground) > 1e-9:
            raise RuntimeError(f"X Bot base bind changed between clips: {clip_id}")
        if rig["names"] != [b.name for b in arm.data.bones]:
            raise RuntimeError(f"X Bot bone order changed: {clip_id}")
        hips = arm.pose.bones['mixamorig:Hips']
        neutral_hips = arm.matrix_world @ arm.data.bones['mixamorig:Hips'].matrix_local.translation
        sample_fps = 30 if authored_action or combat_action else FPS
        count = max(2, math.ceil(duration * sample_fps) + (0 if loop else 1))
        authored_arms = install_authored_arms(arm) if (authored_action and clip_id != "hurt") or clip_id.startswith("longbow_") else {}
        pose_ids = []
        sources[relative] = sha((source / relative).read_bytes())
        hand_reach, hip_lift, left_foot, right_foot = [], [], [], []
        sword_path = []
        foot_tracks = []
        for index in range(count):
            seconds = min(index / sample_fps, duration)
            frame = first if authored_action else first + seconds * source_fps
            bpy.context.scene.frame_set(math.floor(frame), subframe=frame % 1)
            if retarget:
                retarget_pose(arm, animated)
            if clip_id.startswith("longbow_"):
                # The legs own locomotion; the authored weapon layer owns
                # the chest and shoulders instead of inheriting running twists.
                for name in ("Spine", "Spine1", "Spine2", "LeftShoulder", "RightShoulder"):
                    arm.pose.bones["mixamorig:" + name].matrix_basis = Matrix.Identity(4)
                bpy.context.view_layer.update()
            if clip_id == "mounted_idle":
                # Straddle seat, measured against the baked actor-space frame:
                # hip flexion carries the thigh forward and slightly down, the
                # knee hangs the shin vertically, and the mirrored abduction
                # opens the legs around a mount body. Values are the probed
                # pose, not anatomy guesses: thigh fwd +0.395 up -0.168 side
                # +0.105, shin fwd +0.013 up -0.377.
                for side, spread in (("Left", 1.0), ("Right", -1.0)):
                    upper = arm.pose.bones["mixamorig:%sUpLeg" % side]
                    upper.rotation_mode = 'XYZ'
                    upper.rotation_euler.x = 1.1
                    upper.rotation_euler.z = spread * 0.55
                    knee = arm.pose.bones["mixamorig:%sLeg" % side]
                    knee.rotation_mode = 'XYZ'
                    knee.rotation_euler.x = -1.0
                bpy.context.view_layer.update()
            if clip_id == "hurt":
                phase=index/max(count-1,1)
                angle=-.30*math.sin(min(phase/.38,1)*math.pi/2)*(1-phase) + .10*math.sin(phase*math.pi*2)
                for name, weight in (("Spine",.5),("Spine1",.35),("Spine2",.15)):
                    bone=arm.pose.bones["mixamorig:"+name]
                    bone.rotation_mode='XYZ';bone.rotation_euler.x=angle*weight
                bpy.context.view_layer.update()
            depsgraph.update()
            root = arm.matrix_world @ hips.matrix.translation
            hand_reach.append(max(-(arm.matrix_world @ arm.pose.bones["mixamorig:" + side + "Hand"].matrix.translation).y + root.y for side in ("Left", "Right")))
            sword_path.append(arm.matrix_world @ arm.pose.bones["mixamorig:RightHand"].matrix.translation - root)
            hip_lift.append(root.z)
            left_foot.append((arm.matrix_world @ arm.pose.bones["mixamorig:LeftToeBase"].matrix.translation).z)
            right_foot.append((arm.matrix_world @ arm.pose.bones["mixamorig:RightToeBase"].matrix.translation).z)
            # Server owns translation. Keep authored vertical sway in grounded
            # clips; remove the jump's lift because physics already supplies it.
            shift = Vector((root.x - neutral_hips.x, root.y - neutral_hips.y,
                            root.z - neutral_hips.z if clip_id == "jump" else 0))
            foot_tracks.append([tuple((arm.matrix_world @ arm.pose.bones["mixamorig:" + side + "ToeBase"].matrix.translation - shift) * scale) for side in ("Left", "Right")])
            weapon_grip = None
            weapon_basis = Matrix.Identity(3)
            if authored_arms:
                weapon_grip, weapon_basis, support = authored_weapon_pose(base_clip, index/max(count-1,1), authoring, pitch)
                weapon_side = "Left" if base_clip.startswith("longbow_") else "Right"
                support_side = "Right" if weapon_side == "Left" else "Left"
                for side, q in ((weapon_side,weapon_grip),(support_side,support)):
                    authored_arms[side].location = Vector((q.x/scale, -q.z/scale, q.y/scale+ground)) + shift
                bpy.context.view_layer.update();depsgraph.update()
            if weapon_grip is None:
                hand=arm.matrix_world @ arm.pose.bones["mixamorig:RightHand"].matrix.translation - shift
                weapon_grip=Vector((hand.x*scale,(hand.z-ground)*scale,-hand.y*scale))
                weapon_basis=Euler((1.15,0,0)).to_matrix()
                if combat_action or base_clip.startswith("sword_"):
                    # FL-4806: a clip that animates a held sword carries the blade
                    # on the right hand. The fist defines the grip: the blade leaves
                    # it along the pinky-to-index knuckle line and the guard lies
                    # across the finger direction. Bone roll conventions are not used.
                    def knuckle(name):
                        q=arm.matrix_world @ arm.pose.bones["mixamorig:"+name].head
                        return Vector((q.x,q.z,-q.y))
                    wrist_q=knuckle("RightHand")
                    index_q,pinky_q,middle_q=knuckle("RightHandIndex1"),knuckle("RightHandPinky1"),knuckle("RightHandMiddle1")
                    blade=(index_q-pinky_q).normalized()
                    fingers=(middle_q-wrist_q)
                    fingers=(fingers-blade*fingers.dot(blade)).normalized()
                    weapon_basis=Matrix((blade.cross(fingers)*-1,fingers,blade)).transposed()
                    # Grip sits in the closed fist, not at the wrist joint.
                    weapon_grip=weapon_grip+(middle_q-wrist_q)*scale*0.6
            weapon_side = "Left" if base_clip.startswith("longbow_") else "Right"
            support_side = "Right" if weapon_side == "Left" else "Left"
            wrist=arm.matrix_world @ arm.pose.bones["mixamorig:" + weapon_side + "Hand"].matrix.translation - shift
            wrist_actor=Vector((wrist.x*scale,(wrist.z-ground)*scale,-wrist.y*scale))
            grip_error=(wrist_actor-weapon_grip).length
            if authored_arms and grip_error > .065:
                raise RuntimeError(f"Authored weapon grip misses wrist: {clip_id}:{index} error={grip_error}")
            support_wrist=arm.matrix_world @ arm.pose.bones["mixamorig:" + support_side + "Hand"].matrix.translation - shift
            support_actor=Vector((support_wrist.x*scale,(support_wrist.z-ground)*scale,-support_wrist.y*scale))
            support_error=(support_actor-support).length if authored_arms else 0.0
            if authored_arms and support_error > .065:
                raise RuntimeError(f"Authored support misses wrist: {clip_id}:{index} error={support_error}")
            weapon_pose=[*weapon_basis.col[0],*weapon_basis.col[1],*weapon_basis.col[2],*weapon_grip]
            # FL-4907: the frame is 52 bone transforms, never posed face rows.
            frames = [actor_frame(arm, arm.pose.bones[name].matrix, shift, scale, ground)
                      for name in rig["deforming"]]
            transforms = []
            points = []
            for part, (basis, origin) in enumerate(frames):
                transforms.extend(transform_row(basis, origin))
                points.extend(basis @ Vector(v) + origin for v in rig["octahedra"][part])
            if index == 0:
                parity[clip_id] = silhouette_parity(
                    mesh_triangles(meshes, depsgraph, shift, scale, ground, True), rig_triangles(rig, frames))
            payload = struct.pack('<%sf' % len(transforms), *transforms)
            pose_id = f"{clip_id}_frame_{index:03d}"
            poses.append({"pose_id": pose_id, "clip_id": clip_id, "frame_index": index,
                          "weapon_pose": weapon_pose, "right_hand_actor": list(wrist_actor if weapon_side == "Right" else support_actor), "weapon_grip_error": grip_error, "support_hand_error": support_error, "left_hand_actor": list(wrist_actor if weapon_side == "Left" else support_actor),
                          "transform_offset": len(transform_blob) // 4,
                          "local_aabb_min": [min(v[a] for v in points) for a in range(3)],
                          "local_aabb_max": [max(v[a] for v in points) for a in range(3)],
                          "content_sha256": sha(payload),
                          "provenance": {"source_file": relative, "source_sha256": sources[relative],
                                         "sample_seconds": seconds}})
            transform_blob.extend(payload)
            pose_ids.append(pose_id)
        if len({p['content_sha256'] for p in poses if p['clip_id'] == clip_id}) < 2:
            raise RuntimeError(f"Static animation: {clip_id}")
        markers = {}
        if clip_id.startswith("melee_cut"):
            # Contact is the fastest sword-hand frame of the authored cut.
            # The active window closes once the blade has slowed to 40% of
            # that peak. Both come from this clip, never from move timings.
            speeds = [(sword_path[i + 1] - sword_path[i]).length for i in range(len(sword_path) - 1)]
            peak = speeds.index(max(speeds))
            close = peak
            while close + 1 < len(speeds) and speeds[close] > 0.4 * speeds[peak]:
                close += 1
            markers.update(contact=peak / len(speeds), active_end=(close + 1) / len(speeds))
        elif base_clip == "longbow_shot":
            # These are the boundaries of authored_weapon_pose's draw/hold,
            # release and recovery path above. Windup must reach the drawn
            # nock, not stop at 8% of a clip whose draw completes at 58%.
            markers.update(contact=.72, active_end=.76)
        if gait_id.startswith(("walk", "run")):
            markers["left_contact"] = left_foot.index(min(left_foot)) / count
            markers["right_contact"] = right_foot.index(min(right_foot)) / count
        if gait_id.startswith(("walk", "run")):
            # Measure the stance foot's in-place retreat. This is the travel
            # removed by the baked root, not a hand-tuned cycles/second value.
            distances = [0.0]
            for index in range(1, count + 1):
                before, after = foot_tracks[index - 1], foot_tracks[index % count]
                support = min(range(2), key=lambda side: before[side][2] + after[side][2])
                shift = Vector(after[support]) - Vector(before[support])
                retreat = math.hypot(shift.x, shift.y)
                distances.append(distances[-1] + max(retreat, 0.0))
            if distances[-1] <= 0.05:
                raise RuntimeError(f"Missing authored ground travel: {clip_id}")
            markers["cycle_distance_world"] = distances[-1]
            markers["distance_phase"] = [value / distances[-1] for value in distances]
        if clip_id == "jump":
            apex = hip_lift.index(max(hip_lift))
            threshold = hip_lift[0] + (max(hip_lift) - hip_lift[0]) * 0.08
            takeoff = next((i for i in range(apex) if hip_lift[i] > threshold), 0)
            landing = next((i for i in range(apex + 1, count) if hip_lift[i] <= threshold), count - 1)
            markers = {"takeoff": takeoff / (count - 1), "apex": apex / (count - 1), "landing": landing / (count - 1)}
        clips[clip_id] = {"markers": markers,"clip_id": clip_id, "sample_fps": sample_fps, "loop": loop,
                          "authoring": "weapon grip IK and authored sweep/reload" if authored_arms else "source animation", "source_animation": relative, "source_duration_seconds": duration, "bind_retargeted": retarget,
                          "frame_pose_ids": pose_ids}
        print(f"XBOT_CLIP {clip_id} frames={count} duration={duration:.3f} parts={len(rig['deforming'])} "
              f"parity={json.dumps(parity[clip_id])}", flush=True)
    rows, parts = rig["rows"], rig["parts"]
    # RigidMoverGeometry._source_signature: float32 rows then int32 parts.
    mesh_signature = sha(struct.pack('<%sf' % len(rows), *rows) + struct.pack('<%si' % len(parts), *parts))
    leaf_markers = [name for name in rig["names"] if LEAF_MARKER.search(name)]
    mesh_product = {"schema": MESH_SCHEMA, "mesh_key": MESH_KEY, "face_count": len(parts),
                    "part_count": len(rig["deforming"]), "face_rows": rows, "part_indices": parts,
                    "part_bones": rig["deforming"], "source_signature": mesh_signature,
                    "row_layout": "p0.xyz_material, p1.xyz_shade, p2.xyz_part, p2.xyz_zero (degenerate quad)",
                    "provenance": ("FL-4907 XBOT_52_BONE_OCTAHEDRON_RIG_20261002: Mixamo X Bot Standard Rig "
                                   "(Full Fingers), one octahedron per deforming bone in its rest frame; "
                                   "compiled by scripts/bake_xbot_rig_poses.py")}
    mesh_output = PROJECT / MESH_RESOURCE.removeprefix("res://")
    transform_output = PROJECT / TRANSFORM_RESOURCE.removeprefix("res://")
    output.parent.mkdir(parents=True, exist_ok=True)
    mesh_output.write_text(json.dumps(mesh_product, indent=1) + '\n')
    transform_output.write_bytes(transform_blob)
    clip_parity = [v for k, v in parity.items() if k != "bind"]
    product = {"schema": PRODUCT_SCHEMA, "actor_kind": "xbot",
               "mesh_resource": MESH_RESOURCE, "mesh_key": MESH_KEY,
               "mesh_source_signature": mesh_signature,
               "face_count": len(parts), "part_count": len(rig["deforming"]),
               "transform_resource": TRANSFORM_RESOURCE, "transform_sha256": sha(transform_blob),
               "transform_layout": ("float32 little-endian; per pose part_count rows of row-major 3x4 "
                                    "[basis | origin] mapping the part's rest-frame mesh rows to actor "
                                    "space (Y up, ground y=0, X Bot faces +Z); transform_offset counts floats"),
               "poses": poses, "animation_clips": clips, "aim_clips": aim_clips,
               "part_identity": {"encoding": "part index = deforming bone index (RigidMoverGeometry row 2 w)",
                                 "bones": rig["deforming"], "leaf_markers_excluded": leaf_markers},
               "action_clip_aliases": ALIASES,
               "clip_aliases": {**ALIASES, "locomotion": "run", "death": "death", "deflect": "block", "posture_break": "hurt"},
               "provenance": {"source_url": "https://www.mixamo.com/", "character": "X Bot",
                              "source_archive": str(source), "source_sha256": sources,
                              "compiler": "scripts/bake_xbot_rig_poses.py", "blender": bpy.app.version_string,
                              "weapon_source_sha256": sha(weapon_bytes)},
               "geometry_contract": {"construction": "one_octahedron_per_deforming_bone",
                                     "rig": "Mixamo Standard Rig (Full Fingers)",
                                     "source_triangles": original_triangles,
                                     "triangles": len(parts), "parts": len(rig["deforming"]),
                                     "triangles_per_part": TRIANGLES_PER_PART,
                                     "cross_section": "2nd..98th percentile extent of the bone's dominant skin vertices",
                                     "weapons": "separate rigid meshes (assets/actor_mesh/*_weapon.json)",
                                     "standing_height": 1.8, "sample_fps": FPS,
                                     "root_motion": "remove horizontal hips translation; remove jump lift; fixed standing ground origin"},
               "silhouette_parity": {"method": "front (x,y) and side (z,y) %.2f-unit cell coverage, rig vs full source mesh"
                                               % SILHOUETTE_CELL,
                                     "bind": parity["bind"], "clip_frame_0": {k: v for k, v in parity.items() if k != "bind"},
                                     "min_iou": min(min(v["front"]["iou"], v["side"]["iou"]) for v in clip_parity),
                                     "mean_iou": round(sum(v["front"]["iou"] + v["side"]["iou"] for v in clip_parity)
                                                       / (2 * len(clip_parity)), 4),
                                     "bones": rig["bones"]}}
    product['content_sha256'] = sha(json.dumps(product, sort_keys=True, separators=(',', ':')).encode())
    output.write_text(json.dumps(product, indent=2) + '\n')
    print(f"XBOT_BAKED poses={len(poses)} clips={len(clips)} parts={len(rig['deforming'])} "
          f"triangles={len(parts)} transform_bytes={len(transform_blob)} "
          f"min_iou={product['silhouette_parity']['min_iou']} mean_iou={product['silhouette_parity']['mean_iou']} "
          f"product={output} content_sha256={product['content_sha256']} mesh_signature={mesh_signature}", flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'godot_project/assets/actor_mesh/xbot_rig_poses.json')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    compile_product(args.source_dir, args.output)
