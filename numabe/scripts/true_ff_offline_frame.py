#!/usr/bin/env python3
"""FL-4877: run one complete True FF frame offline on the Metal GPU, no Godot.

The production pipeline is replayed command for command:

  P0      TrueFFSceneGBuffer: terrain/static visibility selection, camera
          G-buffer draw, light-map draw and merge (true_ff_gbuffer.glsl,
          true_ff_terrain_visibility.glsl, true_ff_static_visibility.glsl,
          true_ff_light_merge.glsl);
  P1-P4   TrueFFFeaturePasses.configure/reconcile_publication/execute: the
          scene metadata, shadow boundary, owed backstop, native certificate,
          three recorded cut rounds (discover, traverse, witness, seal/admit,
          extract, sites, native, pressure), finish, P3 visibility, history
          and commit;
  P5/P6   TrueFFFeatureRenderer layout and actual RGBA16F glyph paint
          (true_ff_feature_layout.glsl, true_ff_feature_glyphs.glsl).
  PAGE    TrueFFPageBackground atmospheric paper into its observed RGBA16F target.

Programs are the production GLSL with the host's defines, compiled through
scripts/true_ff_offline_shaders.py (the fl4895 GLSL -> SPIR-V -> MSL path).
Every uniform set is checked against the reflected shader layout and every
push block against its reflected size, as RenderingDevice does; a mismatch
is a frame failure, exactly as at launch. The Metal executor
(scripts/true_ff_offline_metal.swift) commits each command alone and samples
META (args slot 54), the P5 control header and the certificate words after
each one, so the first command that sets a fatal bit is named.

The world is scripts/true_ff_offline_world.py: the authoritative A3D terrain
and its compiled bank, the outline language catalog, the canonical airplane
camera of config/godot_proof_pose_registry.json. The live water, effect and
sky families are withdrawn (family mask 0, a production withdrawal variant);
static placements, actors, movers and vegetation are absent. Those are listed
in the receipt under "not_modelled".

usage: python3 scripts/true_ff_offline_frame.py [--project DIR] [--out DIR] [--json]
Exit 0: the page and glyph targets are written and P5 places glyphs. Exit 1: frame rejected,
blank, or a GPU/plan failure (first failing command and bits printed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import true_ff_offline_shaders as shaders  # noqa: E402
import true_ff_offline_world as world  # noqa: E402

GD = world.GD
PROJ = world.PROJ
PASSES_GD_PATH = PROJ / "render/ascii/features/TrueFFFeaturePasses.gd"
PASSES = GD(PASSES_GD_PATH)
RENDERER = world.RENDERER_GD
STRUCTURE = world.STRUCTURE_ABI
SURFACE = world.SURFACE_ABI
GBUFFER = GD(PROJ / "render/ascii/structure/TrueFFSceneGBuffer.gd")
STATIC_VIS = GD(PROJ / "render/ascii/structure/TrueFFStaticVisibility.gd")
TERRAIN_VIS = world.TERRAIN_TREE_GD
WATERFALL = GD(PROJ / "render/effects/water/WaterfallSiteField.gd")
RUN_DIR = ROOT / ".run" / "true_ff_launch_guard" / "offline_frame"
EXECUTOR_SOURCE = ROOT / "scripts" / "true_ff_offline_metal.swift"
GROUPS_MAX = 0xFFFFFFFF  # Godot Metal reports UINT32_MAX for both compute group limits.
NONE = 0xFFFFFFFF
OFFLINE_SHADER_WORKER_CEILING = min(4, os.cpu_count() or 4)
OFFLINE_SHADER_WORKERS = int(os.environ.get(
    "TRUE_FF_OFFLINE_SHADER_WORKERS", str(OFFLINE_SHADER_WORKER_CEILING)
))
if not 1 <= OFFLINE_SHADER_WORKERS <= OFFLINE_SHADER_WORKER_CEILING:
    raise ValueError(
        "TRUE_FF_OFFLINE_SHADER_WORKERS must be between 1 and "
        f"{OFFLINE_SHADER_WORKER_CEILING}"
    )
FATAL_MASK = 0x00FFFFFF
ADMISSION_ERRORS = {1 << 24: "closed_witness", 1 << 25: "native_feasibility",
                    1 << 26: "scene_witness", 1 << 27: "shadow_admission",
                    1 << 28: "scene_depth", 1 << 29: "scene_source", 1 << 31: "capacity_overflow"}
LAYOUT_BITS = {1: "site_capacity", 2: "row_capacity", 4: "page_capacity", 8: "hud",
               16: "native_row", 32: "upstream_rejected", 64: "layout", 128: "refit",
               256: "coverage_glyph", 512: "coverage_slab", 1024: "coverage_refit",
               2048: "face", 4096: "attachment"}


class PlanError(RuntimeError):
    """The host replica refused the frame exactly where Godot would."""


def i32s(*values) -> bytes:
    return struct.pack("<%di" % len(values), *[int(v) for v in values])


u32s = world.u32s
f32s = world.f32s


def _paths(name: str) -> dict:
    """res path constants of the passes host (parsed, never restated)."""
    return {key: "render/" + value.split("res://render/", 1)[1]
            for key, value in PASSES.items() if key.endswith("_PATH") and isinstance(value, str)}


PATHS = _paths("passes")


def _kernel_map(name: str) -> dict:
    # Use the same nested-map resolver as the host/descriptor gate. Stage-shard
    # maps contain entries such as OWED_STAGE_PATHS[1]; truncating those to the
    # nonexistent OWED_STAGE_PATH made the offline frame crash before planning.
    sys.path.insert(0, str(ROOT))
    from scripts import check_true_ff_host_shader_contract as contract
    text = PASSES_GD_PATH.read_text(encoding="utf-8")
    return contract._path_map(text, name, contract._path_constants(text))


EXTRACT = _kernel_map("EXTRACT_KERNELS")
OWED = _kernel_map("OWED_KERNELS")
TRAVERSE = _kernel_map("TRAVERSE_KERNELS")


def _program_list() -> list:
    sys.path.insert(0, str(ROOT))
    from scripts import check_true_ff_host_shader_contract as contract
    return contract._program_paths(PASSES_GD_PATH.read_text(encoding="utf-8"))


# ===================================================================== device

class Device:
    """RenderingDevice-shaped plan recorder with RD's own validation."""

    def __init__(self, work: Path):
        self.work = work
        self.buffers: dict = {}
        self.buffer_flags: dict = {}
        self.textures: dict = {}
        self.programs: dict = {}
        self.metas: dict = {}
        self.bindings: dict = {}
        self.commands: list = []
        self.blob_index = 0
        self.watch = []
        self.dumps = []

    def _blob(self, data: bytes) -> str:
        self.blob_index += 1
        name = f"blob{self.blob_index:05d}.bin"
        (self.work / name).write_bytes(data)
        return name

    def buffer(self, name: str, size: int, data: bytes | None = None, fill: int = 0,
               indirect: bool = False, uniform: bool = False) -> str:
        if size <= 0 or size > 0xFFFFFFFF:
            raise PlanError(f"buffer_size_outside_rd_abi:{name}:{size}")
        if data is not None and len(data) > size:
            raise PlanError(f"buffer_initial_data_exceeds_size:{name}")
        spec = {"size": size, "fill": fill}
        if data:
            spec["file"] = self._blob(data)
        self.buffers[name] = spec
        self.buffer_flags[name] = {"indirect": indirect, "uniform": uniform}
        return name

    def texture(self, name: str, width: int, height: int, fmt: str, data: bytes | None = None) -> str:
        spec = {"width": width, "height": height, "format": fmt}
        if data is not None:
            spec["file"] = self._blob(data)
        self.textures[name] = spec
        return name

    def program(self, name: str, res: str, defines: list | None = None, kind: str = "compute",
                color_formats: list | None = None, depth_compare: str = "less") -> str:
        if name in self.programs:
            return name
        try:
            meta = shaders.compile_program(res, defines or [])
        except shaders.ShaderError as exc:
            raise PlanError(f"pipeline_rejected:{res}:{exc}") from exc
        self.metas[name] = meta
        identity = hashlib.sha256(json.dumps([kind, color_formats, depth_compare]).encode())
        for stage_name in sorted(meta):
            identity.update(Path(meta[stage_name]["metallib"]).read_bytes())
        archive = shaders.CACHE.parent / "archives" / f"{identity.hexdigest()}.binarchive"
        archive.parent.mkdir(parents=True, exist_ok=True)
        if kind == "compute":
            stage = meta["compute"]
            self.programs[name] = {"compute": {"metallib": stage["metallib"], "sets": stage["sets"],
                                               "push_index": stage["push_index"], "threads": stage["threads"]},
                                   "archive": str(archive)}
        else:
            self.programs[name] = {
                "vertex": {"metallib": meta["vertex"]["metallib"], "sets": meta["vertex"]["sets"],
                           "push_index": meta["vertex"]["push_index"]},
                "fragment": {"metallib": meta["fragment"]["metallib"], "sets": meta["fragment"]["sets"],
                             "push_index": meta["fragment"]["push_index"]},
                "color_formats": color_formats, "depth_compare": depth_compare, "archive": str(archive)}
        return name

    def push_bytes(self, program: str) -> int:
        return max(stage["push_bytes"] for stage in self.metas[program].values())

    def uniform_set(self, program: str, set_index: int, entries: dict) -> str:
        """entries: {binding: ("buffer", name) | ("texture", name)}; RD uniform_set_create rules."""
        meta = self.metas[program]
        required: dict = {}
        for stage, stage_meta in meta.items():
            for key, ids in stage_meta["bindings"].items():
                set_text, binding = key.split(":")
                if int(set_text) == set_index:
                    required.setdefault(int(binding), {})[stage] = ids
        supplied = set(entries)
        missing = sorted(set(required) - supplied)
        extra = sorted(supplied - set(required))
        if missing or extra:
            raise PlanError(f"uniform_set_rejected:{program}:set{set_index}:missing={missing}:extra={extra}")
        out = {stage: [] for stage in meta}
        for binding, (kind, name) in entries.items():
            for stage, ids in required[binding].items():
                kinds = [k for _, k in ids]
                if kind == "buffer":
                    if kinds != ["buffer"] or name not in self.buffers:
                        raise PlanError(f"uniform_type_mismatch:{program}:{binding}:{kind}:{kinds}")
                    out[stage].append([ids[0][0], "buffer", name])
                else:
                    if "texture" not in kinds or name not in self.textures:
                        raise PlanError(f"uniform_type_mismatch:{program}:{binding}:{kind}:{kinds}")
                    for ident, k in ids:
                        out[stage].append([ident, k, name])
        key = f"{program}#{set_index}#{len(self.bindings)}"
        self.bindings[key] = {"program": program, "set": set_index, "entries": out}
        return key

    def _push(self, program: str, push: bytes) -> str:
        expected = self.push_bytes(program)
        if len(push) != expected:
            raise PlanError(f"push_constant_size_mismatch:{program}:supplied={len(push)}:required={expected}")
        return push.hex()

    def dispatch(self, label: str, program: str, sets: dict, push: bytes, groups=None, indirect=None) -> None:
        command = {"op": "dispatch", "label": label, "program": program,
                   "sets": {str(k): v for k, v in sets.items()}, "push": self._push(program, push)}
        if indirect is not None:
            buffer, offset = indirect
            if not self.buffer_flags[buffer]["indirect"]:
                raise PlanError(f"indirect_buffer_usage_missing:{buffer}")
            command["indirect"] = [buffer, offset]
        else:
            if groups[0] <= 0 or groups[1] <= 0 or groups[2] <= 0:
                raise PlanError(f"dispatch_group_count_zero:{label}")
            command["groups"] = list(groups)
        self.commands.append(command)

    def draw(self, label: str, program: str, colors: list, depth: str, clear_depth: float,
             sets: dict, items: list) -> None:
        for item in items:
            if "push" in item:
                item["push"] = self._push(program, item["push"])
            if "indirect" in item and not self.buffer_flags[item["indirect"][0]]["indirect"]:
                raise PlanError(f"indirect_buffer_usage_missing:{item['indirect'][0]}")
        self.commands.append({"op": "draw", "label": label, "program": program, "colors": colors,
                              "depth": depth, "clear": True, "clear_depth": clear_depth,
                              "sets": {str(k): v for k, v in sets.items()}, "items": items})

    def upload(self, buffer: str, offset: int, data: bytes, label: str = "") -> None:
        if offset + len(data) > self.buffers[buffer]["size"]:
            raise PlanError(f"buffer_update_outside_buffer:{buffer}")
        if data:
            self.commands.append({"op": "upload", "label": label or f"update:{buffer}", "buffer": buffer,
                                  "offset": offset, "hex": data.hex()} if len(data) <= 4096 else
                                 {"op": "upload", "label": label or f"update:{buffer}", "buffer": buffer,
                                  "offset": offset, "file": self._blob(data)})

    def clear(self, buffer: str, offset: int, size: int, byte: int = 0) -> None:
        if offset + size > self.buffers[buffer]["size"]:
            raise PlanError(f"buffer_clear_outside_buffer:{buffer}")
        if size:
            self.commands.append({"op": "fill", "label": f"clear:{buffer}", "buffer": buffer,
                                  "offset": offset, "size": size, "byte": byte})

    def plan(self) -> dict:
        return {"buffers": self.buffers, "textures": self.textures, "programs": self.programs,
                "bindings": self.bindings, "commands": self.commands, "watch": self.watch,
                "dump": self.dumps,
                "timeout_seconds": 20.0}


# ===================================================================== P0

def heightfield_descriptor(origin, dims, object_id, revision, height_min, height_max) -> bytes:
    """VisibleSurfaceABI.build_heightfield_descriptor (+ water entity at dims_identity.w)."""
    data = bytearray(SURFACE["HEIGHTFIELD_DESCRIPTOR_BYTES"])
    struct.pack_into("<4f", data, 0, origin[0], origin[1], SURFACE["HEIGHTFIELD_CELL_SIZE_XZ"],
                     SURFACE["A3D_SOURCE_HEIGHT_DIVISOR"])
    struct.pack_into("<4f", data, 16, 0.001, height_min, height_max, 0.0)
    struct.pack_into("<4I", data, 32, dims[0], dims[1], object_id, GBUFFER["WATER_ENTITY_ID"])
    struct.pack_into("<4I", data, 48, 1, SURFACE["MAX_TERRAIN_STEPS"], 0, revision)
    return bytes(data)


def pack_prepass_params(matrix, origin, far, forward, is_ortho, epoch, near, light_valid) -> bytes:
    """TrueFFSceneGBuffer._pack_params."""
    out = b"".join(f32s(*column) for column in matrix)
    out += f32s(origin[0], origin[1], origin[2], far)
    out += u32s(1 if is_ortho else 0, epoch & 0x7FFFFFFF, 0, 1 if light_valid else 0)
    out += f32s(forward[0], forward[1], forward[2], near)
    return out


def pack_frame_params(camera: dict, sun: tuple, epoch: int) -> bytes:
    """TrueFFStructureABI.pack_params for the canonical offline camera."""
    inverse = camera["inv_view_proj"]
    extent = camera["page_extent"]
    translation = camera["translation"]
    out = b"".join(f32s(*column) for column in inverse)
    out += f32s(*camera["origin"], camera["camera_near"])
    out += f32s(*camera["forward"], camera["camera_far"])
    out += f32s(*sun, 0.0)
    out += u32s(extent[0], extent[1], 1 if camera["is_ortho"] else 0, epoch & 0x7FFFFFFF)
    out += f32s(camera["frame_time_seconds"], 0.0, 0.0, 0.0)
    reverse = matrix_is_reverse_z(camera["world_to_clip"], camera["origin"], camera["forward"],
                                  camera["camera_near"], camera["camera_far"])
    out += f32s(camera["tan_half_fov"], camera["ortho_half_height"], 1.0 if reverse else 0.0, 0.0)
    out += i32s(translation[0], translation[1], 0, 0)
    out += b"".join(f32s(*column) for column in camera["world_to_clip"])
    if len(out) != int(STRUCTURE["PARAMS_BYTES"]):
        raise PlanError(f"frame_params_size_mismatch:{len(out)}")
    return out


def pack_atmosphere_facts(sun: tuple, language: dict) -> bytes:
    """TrueFFPageBackground.pack_push noon facts consumed by P6."""
    out = bytearray(64)
    struct.pack_into("<4f", out, 0, *sun, 0.0)
    struct.pack_into("<4f", out, 16, 0.0, -1.0, 0.0, 0.0045)
    struct.pack_into("<4f", out, 32, 0.0, float(language["full_advance"]),
                     float(language["row_pitch"]), 0.0)
    struct.pack_into("<4I", out, 48, 0, 0, 1, 0)
    return bytes(out)


def matrix_is_reverse_z(matrix, origin, forward, near, far) -> bool:
    length = math.sqrt(sum(v * v for v in forward))
    direction = [v / length for v in forward]

    def clip(point):
        return [sum(matrix[c][r] * (point[c] if c < 3 else 1.0) for c in range(4)) for r in range(4)]
    near_point = [origin[i] + direction[i] * max(near * 4.0, 0.5) for i in range(3)]
    far_point = [origin[i] + direction[i] * max(far * 0.5, 1.0) for i in range(3)]
    a, b = clip(near_point), clip(far_point)
    if abs(a[3]) <= 1e-9 or abs(b[3]) <= 1e-9:
        return False
    return a[2] / a[3] > b[2] / b[3]


def light_view(camera: dict, to_light, slab, domain, caster_bounds) -> dict:
    """TrueFFSceneGBuffer.light_view."""
    size = 1024  # LIGHT_MAP_SIZE
    light = [v / math.sqrt(sum(c * c for c in to_light)) for v in to_light]
    if light[1] <= 0.0:
        return {"valid": False, "reason": "shadow_light_below_horizon"}

    def cross(a, b):
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]

    def norm(v):
        length = math.sqrt(sum(c * c for c in v))
        return [c / length for c in v]

    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))
    x_axis = cross(light, [0.0, 1.0, 0.0])
    x_axis = norm(x_axis) if dot(x_axis, x_axis) > 1e-12 else [1.0, 0.0, 0.0]
    y_axis = norm(cross(light, x_axis))
    w2c = camera["world_to_clip"]
    w = [w2c[0][3], w2c[1][3], w2c[2][3], w2c[3][3]]
    planes = []
    for axis in range(2):
        row = [w2c[0][axis], w2c[1][axis], w2c[2][axis], w2c[3][axis]]
        for side in ([w[i] + row[i] for i in range(4)], [w[i] - row[i] for i in range(4)]):
            normal = side[:3]
            length = math.sqrt(dot(normal, normal))
            planes.append(([c / length for c in normal], -side[3] / length))
    forward = norm(camera["forward"])
    eye = camera["origin"]
    planes.append((forward, dot(forward, eye) + camera["camera_near"]))
    planes.append(([-c for c in forward], -(dot(forward, eye) + camera["camera_far"])))
    planes.append(([0.0, 1.0, 0.0], slab[0]))
    planes.append(([0.0, -1.0, 0.0], -slab[1]))

    def intersect(p1, p2, p3):
        n1, d1 = p1
        n2, d2 = p2
        n3, d3 = p3
        denom = dot(n1, cross(n2, n3))
        if abs(denom) < 1e-12:  # Godot Plane.intersect_3 CMP_EPSILON
            return None
        a, b, c = cross(n2, n3), cross(n3, n1), cross(n1, n2)
        return [(d1 * a[i] + d2 * b[i] + d3 * c[i]) / denom for i in range(3)]
    found = False
    low = [0.0, 0.0]
    high = [0.0, 0.0]
    for i in range(len(planes)):
        for j in range(i + 1, len(planes)):
            for k in range(j + 1, len(planes)):
                point = intersect(planes[i], planes[j], planes[k])
                if point is None:
                    continue
                if any(dot(n, point) - d < -1e-3 * max(1.0, abs(d)) for n, d in planes):
                    continue
                projected = [dot(point, x_axis), dot(point, y_axis)]
                if not found:
                    low, high, found = list(projected), list(projected), True
                else:
                    low = [min(low[0], projected[0]), min(low[1], projected[1])]
                    high = [max(high[0], projected[0]), max(high[1], projected[1])]
    if not found:
        return {"valid": False, "reason": "shadow_receiver_footprint_empty"}
    extent = max(high[0] - low[0], high[1] - low[1]) * size / (size - 2)
    side = 2.0 ** math.ceil(math.log(extent) / math.log(2.0))
    texel = side / size
    centre = [(low[0] + high[0]) * 0.5, (low[1] + high[1]) * 0.5]
    u0 = math.floor((centre[0] - 0.5 * side) / texel) * texel
    v0 = math.floor((centre[1] - 0.5 * side) / texel) * texel
    depth_low, depth_high = math.inf, -math.inf
    for v in (v0, v0 + side):
        for height in slab:
            d = (height - v * y_axis[1]) / light[1]
            depth_low, depth_high = min(depth_low, d), max(depth_high, d)
    bounds_low, bounds_size = caster_bounds
    for corner in range(8):
        point = [bounds_low[a] + (bounds_size[a] if corner & (1 << a) else 0.0) for a in range(3)]
        d = dot(point, light)
        depth_low, depth_high = min(depth_low, d), max(depth_high, d)
    depth_low -= texel
    depth_high += texel
    depth_range = depth_high - depth_low
    scale = 2.0 / side
    row0 = [scale * x_axis[0], scale * x_axis[1], scale * x_axis[2], -scale * u0 - 1.0]
    row1 = [scale * y_axis[0], scale * y_axis[1], scale * y_axis[2], -scale * v0 - 1.0]
    row2 = [-light[0] / depth_range, -light[1] / depth_range, -light[2] / depth_range, depth_high / depth_range]
    matrix = [[row0[0], row1[0], row2[0], 0.0], [row0[1], row1[1], row2[1], 0.0],
              [row0[2], row1[2], row2[2], 0.0], [row0[3], row1[3], row2[3], 1.0]]
    u_centre, v_centre = u0 + 0.5 * side, v0 + 0.5 * side
    origin = [x_axis[i] * u_centre + y_axis[i] * v_centre + light[i] * depth_high for i in range(3)]
    return {"valid": True, "view_projection": matrix, "origin": origin, "forward": [-c for c in light],
            "far": depth_range}


class PrePass:
    """TrueFFSceneGBuffer + TrueFFTerrainVisibility + TrueFFStaticVisibility (cold, terrain world)."""

    def __init__(self, device: Device, terrain: dict, size: tuple):
        self.d = device
        self.t = terrain
        self.size = size
        d = device
        for name, (w, h) in {"p0": size, "light": (1024, 1024), "light_static": (1024, 1024),
                             "light_overlay": (1024, 1024)}.items():
            d.texture(f"{name}_depth", w, h, "d32f")
            d.texture(f"{name}_ids", w, h, "rgba32u")
            d.texture(f"{name}_primitive", w, h, "rg32u")
        d.buffer("p0_params", GBUFFER["PARAMS_BYTES"], uniform=True)
        d.buffer("p0_light_params", GBUFFER["PARAMS_BYTES"], uniform=True)
        d.buffer("p0_heightfield", SURFACE["HEIGHTFIELD_DESCRIPTOR_BYTES"], uniform=True)
        d.buffer("p0_actor_control", 16, uniform=True)
        import re
        names = re.findall(r'"(\w+)"', re.search(r"^const BANK_NAMES := \[(.*?)\]",
                           (PROJ / "render/ascii/structure/TrueFFSceneGBuffer.gd").read_text(), re.M | re.S).group(1))
        self.banks = {name: d.buffer(f"p0_bank_{name}", GBUFFER["MIN_BANK_BYTES"]) for name in names}
        d.texture("p0_placeholder_float", 1, 1, "r32f", bytes(4))
        d.texture("p0_placeholder_uint", 1, 1, "r8u", bytes(1))
        d.buffer("p0_vegetation_frame", 32, uniform=True)
        width, height = terrain["dims"]
        corner = struct.pack("<%df" % len(terrain["heights"]), *terrain["heights"])
        d.texture("terrain_heights", width + 1, height + 1, "r32f", corner)
        d.texture("terrain_materials", width, height, "r8u", bytes(terrain["materials"]))
        d.texture("terrain_elevations", width, height, "r8u", bytes(terrain["elevations"]))
        # Terrain visibility (two instances: camera and light).
        leaf = TERRAIN_VIS["LEAF"]
        chunks = (max(width, height) + leaf - 1) // leaf
        self.leaf_side = 1
        while self.leaf_side < chunks:
            self.leaf_side *= 2
        nodes = (4 * self.leaf_side * self.leaf_side - 1) // 3
        for view in ("camera", "light"):
            d.buffer(f"tv_{view}_tree", nodes * 16)
            d.buffer(f"tv_{view}_cells", 16 + max(width * height, 1) * 4, indirect=True)
        self.tv_program = d.program("terrain_visibility", "render/ascii/structure/true_ff_terrain_visibility.glsl")
        self.tv_dirty = {"camera": True, "light": True}
        # Static visibility: no static placement is published (empty registry).
        d.buffer("sv_masks", 16)
        d.buffer("sv_asset_classes", 16)
        for front in range(2):
            d.buffer(f"sv_queue{front}", 16 + 1 * 8, indirect=True)
        for selection in range(3):
            d.buffer(f"sv_output{selection}", STRUCTURE["P0_STATIC_FACE_HEADER_BYTES"] + 1 * STRUCTURE["P0_STATIC_FACE_BYTES"],
                     indirect=True)
        d.buffer("sv_sort_args", 16, indirect=True)
        d.buffer("sv_histograms", 1 * 256 * 4)
        d.buffer("sv_info", 32, u32s(0, 0, 0, GROUPS_MAX) + f32s(0.0) + bytes(12), uniform=True)
        self.sv_program = d.program("static_visibility", "render/ascii/structure/true_ff_static_visibility.glsl")
        self.sv_sets = []
        for selection in range(3):
            pair = []
            for front in range(2):
                resources = ["bank_static_nodes", "bank_static_leaf_ids", self.banks["instances"], self.banks["assets"],
                             self.banks["static_blas_nodes"], self.banks["local_rows"], "sv_asset_classes",
                             f"sv_queue{front}", f"sv_queue{1 - front}", self.banks["static_blas_ids"],
                             f"sv_output{selection}", "sv_masks"]
                entries = {0: ("buffer", "p0_params" if selection == 0 else "p0_light_params")}
                for index, name in enumerate(resources):
                    entries[index + 1] = ("buffer", name)
                entries.update({13: ("buffer", "sv_info"), 14: ("buffer", "sv_sort_args"),
                                15: ("buffer", "sv_histograms")})
                pair.append(d.uniform_set(self.sv_program, 0, entries))
            self.sv_sets.append(pair)
        # G-buffer programs: the dry variant (water withdrawn) for both depth conventions.
        gbuffer = "render/ascii/structure/true_ff_gbuffer.glsl"
        colors = ["rgba32u", "rg32u"]
        self.draw_programs = {
            False: d.program("gbuffer_dry", gbuffer, ["TFF_WITHDRAWN_WATER"], "render", colors, "less"),
            True: d.program("gbuffer_dry_reverse", gbuffer, ["TFF_WITHDRAWN_WATER"], "render", colors, "greater")}
        self.merge_program = d.program("light_merge", "render/ascii/structure/true_ff_light_merge.glsl",
                                       [], "render", colors, "always")
        merge = {}
        for binding, name in enumerate(["light_static_depth", "light_static_ids", "light_static_primitive",
                                        "light_overlay_depth", "light_overlay_ids", "light_overlay_primitive"]):
            merge[binding] = ("texture", name)
        self.merge_set = d.uniform_set(self.merge_program, 0, merge)

    def _gbuffer_set(self, program: str, params: str, cells: str, static_faces: str) -> str:
        b = self.banks
        entries = {0: ("buffer", params), 2: ("buffer", b["instances"]), 3: ("buffer", b["assets"]),
                   4: ("buffer", b["local_rows"]), 5: ("buffer", "p0_heightfield"),
                   6: ("texture", "terrain_heights"), 7: ("texture", "terrain_materials"),
                   8: ("buffer", b["movers"]), 9: ("buffer", b["mover_rows"]), 10: ("buffer", "p0_actor_control"),
                   11: ("buffer", b["actor_instances"]), 12: ("buffer", b["actor_rows"]),
                   13: ("buffer", b["water_spans"]), 22: ("buffer", cells),
                   23: ("buffer", b["rigid_primitive_faces"]),
                   STRUCTURE["P0_STATIC_FACE_BINDING"]: ("buffer", static_faces)}
        for binding in (115, 116, 117):
            entries[binding] = ("buffer", b["mover_rows"])
        entries.update(self.vegetation_entries())
        return self.d.uniform_set(program, 0, entries)

    def vegetation_entries(self) -> dict:
        """vegetation_resources(scene): the inert set (no runtime publication)."""
        entries = {104: ("texture", "p0_placeholder_float"), 105: ("buffer", "p0_vegetation_frame"),
                   114: ("buffer", "p0_vegetation_frame")}
        for binding in (109, 110, 111, 112, 113):
            entries[binding] = ("buffer", self.banks["mover_rows"])
        return entries

    def prepare(self, revision: int) -> None:
        t = self.t
        descriptor = heightfield_descriptor(t["origin"], t["dims"], STRUCTURE["P0_TERRAIN_OBJECT_ID"], revision,
                                            t["height_min"], t["height_max"])
        self.d.upload("p0_heightfield", 0, descriptor)

    def _terrain_select(self, view: str, params: str) -> None:
        d = self.d
        entries = {0: ("buffer", params), 1: ("buffer", "p0_heightfield"), 2: ("texture", "terrain_heights"),
                   3: ("buffer", f"tv_{view}_tree"), 4: ("buffer", f"tv_{view}_cells")}
        key = d.uniform_set(self.tv_program, 0, entries)

        def dispatch(operation, side):
            groups = max((side * side + 63) // 64, 1)
            d.dispatch(f"P0 terrain_visibility[{view}] op{operation} side{side}", self.tv_program, {0: key},
                       i32s(operation, self.leaf_side, side, 0), groups=(groups, 1, 1))
        if self.tv_dirty[view]:
            dispatch(0, self.leaf_side)
            side = self.leaf_side // 2
            while side >= 1:
                dispatch(1, side)
                side //= 2
            self.tv_dirty[view] = False
        dispatch(2, self.leaf_side)
        side = self.leaf_side // 2
        while side >= 1:
            dispatch(3, side)
            side //= 2
        dispatch(4, self.leaf_side)

    def _static_select(self, selection: int) -> None:
        d = self.d
        sets = self.sv_sets[selection]

        def push(operation, shift=0, word=0):
            return i32s(operation, selection, shift, word)
        d.dispatch(f"P0 static_visibility[{selection}] op0", self.sv_program, {0: sets[0]}, push(0), groups=(1, 1, 1))
        front = 0  # tree depth 0: no static leaves
        d.dispatch(f"P0 static_visibility[{selection}] op3", self.sv_program, {0: sets[front]}, push(3), groups=(1, 1, 1))
        for digit in range(8):
            for operation in range(4, 8):
                label = f"P0 static_visibility[{selection}] sort digit{digit} op{operation}"
                if operation == 5:
                    d.dispatch(label, self.sv_program, {0: sets[front]}, push(operation, (digit % 4) * 8, digit // 4),
                               groups=(1, 1, 1))
                else:
                    d.dispatch(label, self.sv_program, {0: sets[front]}, push(operation, (digit % 4) * 8, digit // 4),
                               indirect=("sv_sort_args", 0))

    def draw(self, camera: dict, epoch: int) -> bool:
        d = self.d
        reverse = matrix_is_reverse_z(camera["world_to_clip"], camera["origin"], camera["forward"],
                                      camera["camera_near"], camera["camera_far"])
        d.upload("p0_params", 0, pack_prepass_params(camera["world_to_clip"], camera["origin"], camera["camera_far"],
                                                     camera["forward"], False, epoch, camera["camera_near"], False))
        self._static_select(0)
        self._terrain_select("camera", "p0_params")
        program = self.draw_programs[reverse]
        key = self._gbuffer_set(program, "p0_params", "tv_camera_cells", "sv_output0")
        items = [{"push": i32s(GBUFFER["FAMILY_TERRAIN"], 0, 0, 0), "indirect": ["tv_camera_cells", 0]},
                 {"push": i32s(GBUFFER["FAMILY_STATIC"], 0, 0, 0), "indirect": ["sv_output0", 0]}]
        d.draw("P0 gbuffer camera draw", program, ["p0_ids", "p0_primitive"], "p0_depth",
               0.0 if reverse else 1.0, {0: key}, items)
        return reverse

    def draw_light(self, camera: dict, sun, epoch: int) -> dict:
        d = self.d
        t = self.t
        slab = (t["height_min"], t["height_max"])
        caster = ((t["origin"][0], t["height_min"], t["origin"][1]),
                  (t["dims"][0], t["height_max"] - t["height_min"], t["dims"][1]))
        view = light_view(camera, sun, slab, None, caster)
        if not view["valid"]:
            return view
        d.upload("p0_light_params", 0, pack_prepass_params(view["view_projection"], view["origin"], view["far"],
                                                           view["forward"], True, epoch, 0.0, True))
        self._terrain_select("light", "p0_light_params")
        self._static_select(1)
        program = self.draw_programs[False]
        key = self._gbuffer_set(program, "p0_light_params", "tv_light_cells", "sv_output1")
        d.draw("P0 light static draw", program, ["light_static_ids", "light_static_primitive"], "light_static_depth",
               1.0, {0: key}, [{"push": i32s(GBUFFER["FAMILY_TERRAIN"], 0, 0, 0), "indirect": ["tv_light_cells", 0]},
                               {"push": i32s(GBUFFER["FAMILY_STATIC"], 0, 0, 0), "indirect": ["sv_output1", 0]}])
        self._static_select(2)
        key = self._gbuffer_set(program, "p0_light_params", "tv_light_cells", "sv_output2")
        d.draw("P0 light animated draw", program, ["light_overlay_ids", "light_overlay_primitive"],
               "light_overlay_depth", 1.0, {0: key},
               [{"push": i32s(GBUFFER["FAMILY_STATIC"], 0, 0, 0), "indirect": ["sv_output2", 0]}])
        d.draw("P0 light merge", self.merge_program, ["light_ids", "light_primitive"], "light_depth", 1.0,
               {0: self.merge_set}, [{"instances": 1, "vertices": 6}])
        return view


# ===================================================================== P1-P4

class Passes:
    """TrueFFFeaturePasses for one view, cold publication, the family mask given."""

    def __init__(self, device: Device, terrain: dict, language: dict, mask: int):
        self.d = device
        self.t = terrain
        self.lang = language
        self.mask = mask
        self.programs: dict = {}
        self.sets: dict = {}
        self.recording_cut = False
        self.epoch = 0
        self.groups_x = GROUPS_MAX
        native = PATHS["NATIVE_PATH"]
        jobs = [(native, [])]
        for path in _program_list():
            if path == native:
                continue
            effective = self._path_family_mask(path)
            jobs.append((path, [name for bit, name in ((1, "TFF_WITHDRAWN_WATER"), (2, "TFF_WITHDRAWN_EFFECTS"),
                                                       (4, "TFF_WITHDRAWN_SKY")) if not effective & bit]))
        # Cache-fill with the same bounded compiler-service pressure as the
        # launch gate. Apple backend workers contend with each other; a
        # host-core-sized pool makes admission slower and starves the desktop.
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=OFFLINE_SHADER_WORKERS) as pool:
            failures = [f"{job[0]}: {exc}" for job, exc in zip(jobs, pool.map(self._try_compile, jobs)) if exc]
        if failures:
            raise PlanError("pipeline_rejected: " + "; ".join(failures[:3]))
        self.programs[native] = device.program("native", native)
        for path, defines in jobs[1:]:
            self.programs[path] = device.program(Path(path).stem + "#" + str(self._path_family_mask(path)),
                                                 path, defines)
        template = bytearray(PASSES["SLOT_COUNT"] * 16)
        for slot in range(PASSES["SLOT_COUNT"]):
            if slot in (PASSES["SLOT_META"], 59):
                continue
            struct.pack_into("<2I", template, slot * 16 + 4, 1, 1)
        self.args_template = bytes(template)

    @staticmethod
    def _try_compile(job) -> str:
        try:
            shaders.compile_program(job[0], job[1])
            return ""
        except shaders.ShaderError as exc:
            return str(exc)

    def _path_family_mask(self, path: str) -> int:
        P = PATHS
        if path in (P["OWED_INIT_PATH"],
                    P["TRAVERSE_DEMAND_PATH"], P["TRAVERSE_EXPLORE_PATH"]):
            return 0
        for bit, family in ((1, "WATER"), (2, "EFFECT"), (4, "SKY")):
            if path in (P[f"OWED_TRANSPORT_{family}_PATH"], P[f"OWED_PROBE_{family}_PATH"],
                        P[f"OWED_RESTORE_{family}_PATH"], P[f"TRAVERSE_DEMAND_{family}_PATH"],
                        P[f"TRAVERSE_EXPLORE_{family}_PATH"]):
                return self.mask & bit
        return self.mask

    # ---------------------------------------------------------- preparation
    def prepare(self, page_size: tuple, viewport: tuple, prepass_size: tuple, p0: PrePass) -> None:
        d, t, lang = self.d, self.t, self.lang
        facts = t["facts"]
        hierarchy, ingress = facts["hierarchy"], facts["scene_ingress"]
        self.page = page_size
        self.viewport = viewport
        # _scene_publication with no static leaves.
        roots = hierarchy["root_count"]
        leaf_capacity, nonstatic_depth = 1, 1
        while leaf_capacity < roots:
            leaf_capacity *= 2
            nonstatic_depth += 1
        heap = 2 * leaf_capacity - 1 if roots > 0 else 0
        static_nodes, leaves = 0, 0
        span = static_nodes + leaves
        metadata = span + heap + 1
        base = facts["admission_node_total"]
        depth = max(0, nonstatic_depth if roots > 0 else 0) + 1
        if depth > PASSES["SCENE_DEPTH"]:
            raise PlanError("scene_metadata_unsigned_or_identity_abi_invalid")
        self.scene = {"static_nodes": static_nodes, "static_span": span, "metadata": metadata,
                      "leaf_capacity": leaf_capacity, "heap": heap, "nonstatic_depth": nonstatic_depth,
                      "depth": depth, "admission_base": base, "admission_bound": base + metadata,
                      "changed_capacity": max(ingress["root_capacity"], 1)}
        self.scene_levels = depth
        # _ensure_buffers
        minimum_advance = PASSES["SHADOW_ACCOUNT_ADVANCE"]
        candidates = max(math.ceil(page_size[0] / minimum_advance), 1) * max(math.ceil(page_size[1] / lang["row_pitch"]), 1)
        levels_cap = world.FEATURE_ABI["HIERARCHY_LEVELS"]  # TrueFFFeaturePasses.MAX_LEVELS
        if candidates > 0xFFFFFFFF // PASSES["SITE_BYTES"] or candidates > 0xFFFFFFFF // (levels_cap * 16) \
                or candidates > 0xFFFFFFFF // PASSES["WITNESS_LANE_BYTES"]:
            raise PlanError("page_budget_exceeds_buffer_abi")
        native_rows = math.ceil(page_size[1] / lang["row_pitch"])
        pixels = prepass_size[0] * prepass_size[1]
        admission_bytes = world.FEATURE_ABI["ADMISSION_NODE_BYTES"]
        self.caps = {"native_rows": native_rows, "native_span": 2 * candidates,
                     "frontier": candidates, "leaves": candidates, "admitted": candidates,
                     "crossings": candidates, "segments": candidates, "candidates": candidates,
                     "sites": candidates, "slot_map": candidates,
                     "instances": max(max(facts["instance_capacity"], facts["instance_count"]), 1),
                     "edge_faces": max(facts["feature_total"], 1),
                     "admission_nodes": self.scene["admission_bound"], "scene_metadata": metadata,
                     "scene_heap": max(heap, 1), "scene_changed_capacity": self.scene["changed_capacity"],
                     "shadow_pixels": pixels}
        self.phase = (candidates, candidates)
        c = self.caps
        site_bytes, crossing = PASSES["SITE_BYTES"], PASSES["CROSSING_BYTES"]
        sizes = {
            "frontier0": candidates * levels_cap * 16, "frontier1": candidates * 16,
            "scene0": candidates * 16, "scene1": candidates * 16, "scene_info": PASSES["SCENE_INFO_BYTES"],
            "scene_nodes": metadata * PASSES["SCENE_NODE_BYTES"], "scene_bounds": c["scene_heap"] * PASSES["SCENE_NODE_BYTES"],
            "instance_scene": c["instances"] * 8, "scene_dirty_nodes": c["scene_heap"] * 4,
            "scene_dirty_args": PASSES["SCENE_DEPTH"] * 16, "scene_changed_sources": c["scene_changed_capacity"] * 4,
            "leaves": candidates * 16, "admitted": candidates * 16, "crossings": candidates * crossing,
            "slot_map": candidates * 8, "links": candidates * 8, "slot_keys": candidates * 16,
            "jump0": candidates * PASSES["JUMP_BYTES"], "jump1": candidates * PASSES["JUMP_BYTES"],
            "segments": candidates * PASSES["SEGMENT_BYTES"], "candidates": candidates * site_bytes,
            "sites": candidates * site_bytes, "native_control": (8 + 4 * native_rows) * 4,
            "native_rows": 5 * candidates * 4, "native_placements": 2 * candidates * 96,
            "native_coverage": 2 * candidates * 32, "owed": 64 + 2 * candidates * 160,
            "native_plans": candidates * site_bytes, "native_plan_control": 32,
            "edge_faces": c["edge_faces"] * 8, "admission_nodes": c["admission_nodes"] * admission_bytes,
            "instance_nodes": c["instances"] * 8, "shadow_pixels": pixels * 16,
            "phase_history": (self.phase[0] + self.phase[1]) * PASSES["PHASE_HISTORY_BYTES"] + 8,
            "args": PASSES["SLOT_COUNT"] * 16, "camera": PASSES["CAMERA_BYTES"],
            "retry_dispatch": (4 + 6 * 3 + PASSES["SLOT_COUNT"] * 3 + 6) * 4,
            "witness_lanes": candidates * PASSES["WITNESS_LANE_BYTES"],
        }
        indirect = {"args", "native_control", "native_plan_control", "scene_dirty_args", "owed", "retry_dispatch"}
        for name, size in sizes.items():
            d.buffer(f"ps_{name}", size, self.args_template if name == "args" else None,
                     fill=0xFF if name == "edge_faces" else 0, indirect=name in indirect,
                     uniform=name in ("camera", "scene_info"))
        self.b = {name: f"ps_{name}" for name in sizes}
        self._ensure_sets(p0)
        self._ensure_discovery_set()
        self._ensure_native_set()
        # reconcile_publication: scene info, history, node ranges, tile, adjacency, scene metadata.
        d.upload(self.b["scene_info"], 0, self._pack_scene_info())
        for name in ("admission_nodes", "phase_history"):
            d.clear(self.b[name], 0, sizes[name])
        d.clear(self.b["retry_dispatch"], 0, 16)
        d.clear(self.b["owed"], 0, 64)
        d.upload(self.b["instance_nodes"], 0, facts["instance_node_ranges"])
        d.upload(self.b["camera"], 204, i32s(self._i32(self.groups_x)))
        d.clear(self.b["edge_faces"], 0, sizes["edge_faces"], 0xFF)
        for asset in facts["assets"]:
            if asset["face_count"] > 0:
                raise PlanError("offline_adjacency_for_mesh_assets_not_modelled")
        # _prepare_scene_metadata (topology changed)
        d.upload(self.b["camera"], 156, i32s(self.scene["admission_bound"]))
        d.upload(self.b["camera"], 192, i32s(facts["instance_count"], 0, facts["max_level"], self._i32(self.groups_x)))
        d.upload(self.b["args"], 0, self.args_template)
        scene = PATHS["SCENE_PATH"]
        self._host_count(scene, [0, 0, 0, 0], max(metadata, facts["instance_count"]), "P1 scene metadata init")
        self._host_count(scene, [1, 0, 0, 0], metadata, "P1 scene metadata link")
        self._host_count(scene, [5, 0, 0, 0], metadata, "P1 scene metadata seal")
        self._scene_bounds(hierarchy["root_count"], True)
        d.clear(self.b["admission_nodes"], base * admission_bytes, metadata * admission_bytes)

    @staticmethod
    def _i32(value: int) -> int:
        return value - 0x100000000 if value >= 0x80000000 else value

    def _scene_bounds(self, changed: int, initial: bool) -> None:
        scene = PATHS["SCENE_PATH"]
        self._host_count(scene, [4, 0, 0, 0], PASSES["SCENE_DEPTH"], "P1 scene bounds reset")
        self._host_count(scene, [2, 0, changed, 1 if initial else 0], changed, "P1 scene bounds leaves")
        for depth in range(self.scene["nonstatic_depth"] - 2, -1, -1):
            self.d.dispatch(f"P1 scene bounds depth{depth}", self.programs[scene], self._set_keys(scene),
                            i32s(3, depth, 0, 0), indirect=(self.b["scene_dirty_args"], depth * 16))

    def _pack_scene_info(self) -> bytes:
        facts = self.t["facts"]
        hierarchy, ingress = facts["hierarchy"], facts["scene_ingress"]
        s = self.scene
        info = u32s(s["static_nodes"], hierarchy["static_leaf_count"], hierarchy["root_count"], facts["node_total"])
        info += f32s(hierarchy["static_max_displacement"]) + u32s(1 if hierarchy["static_bounds_unbounded"] else 0,
                                                                  s["depth"], hierarchy["generation"])
        info += u32s(s["metadata"], s["static_span"], s["leaf_capacity"], s["admission_base"],
                     ingress["namespaces"][0], ingress["namespaces"][1], hierarchy["generation"],
                     max(ingress["serial"], 1))
        return info

    def _ensure_sets(self, p0: PrePass) -> None:
        d, b = self.d, self.b
        order = {4: "edge_faces", 5: "camera", 6: "frontier0", 7: "frontier1", 8: "leaves", 10: "admitted",
                 11: "crossings", 12: "slot_map", 13: "links", 14: "jump0", 15: "jump1", 16: "segments",
                 17: "candidates", 18: "sites", 19: "args", 44: "scene_info", 45: "scene_nodes",
                 46: "scene_bounds", 47: "instance_scene", 54: "native_plans", 55: "native_plan_control", 59: "owed"}
        witness = {TRAVERSE[stage] for stage in (26, 27, 33, 34, 44)}
        for path, program in self.programs.items():
            if path in (PATHS["DISCOVER_PATH"], PATHS["NATIVE_PATH"]):
                continue
            effective = self._path_family_mask(path)
            if effective:
                raise PlanError("offline_live_source_set1_not_modelled")
            entries = {0: ("buffer", "bank_nodes"), 1: ("buffer", "bank_features"), 2: ("buffer", "bank_faces"),
                       3: ("buffer", "bank_instances")}
            for binding, name in order.items():
                entries[binding] = ("buffer", b[name])
            entries.update({20: ("texture", "p0_depth"), 21: ("texture", "p0_ids"), 36: ("texture", "p0_primitive"),
                            37: ("buffer", "bank_surface_topology"), 38: ("buffer", "bank_surface_instances"),
                            40: ("buffer", b["shadow_pixels"]), 64: ("buffer", "bank_figure_bindings"),
                            65: ("buffer", b["retry_dispatch"])})
            if path in witness:
                entries[66] = ("buffer", b["witness_lanes"])
            if path in TRAVERSE.values() and path not in witness:
                entries[60] = ("buffer", b["native_control"])
            entries.update({41: ("texture", "light_depth"), 42: ("texture", "light_ids"),
                            43: ("buffer", "p0_light_params"), 48: ("buffer", "bank_static_nodes"),
                            49: ("buffer", "bank_roots")})
            if path == PATHS["SCENE_PATH"]:
                entries[50] = ("buffer", "bank_static_leaf_instances")
                for binding, name in {51: "scene_dirty_nodes", 52: "scene_dirty_args", 53: "scene_changed_sources"}.items():
                    entries[binding] = ("buffer", b[name])
            if path in OWED.values():
                for binding, name in {60: "native_control", 61: "native_rows", 62: "native_placements",
                                      63: "native_coverage"}.items():
                    entries[binding] = ("buffer", b[name])
            entries.update({22: ("buffer", "bank_terrain_classes"), 23: ("texture", "terrain_heights"),
                            24: ("texture", "terrain_materials"), 25: ("buffer", "bank_terrain_params"),
                            27: ("buffer", b["admission_nodes"]), 28: ("buffer", b["instance_nodes"]),
                            30: ("buffer", b["slot_keys"]), 35: ("buffer", b["phase_history"])})
            # VegetationFeatures.frame_uniforms: bank metadata + P0 rows/instances/assets + P0's set.
            entries.update({31: ("buffer", "bank_vegetation_features"), 32: ("buffer", p0.banks["local_rows"]),
                            33: ("buffer", p0.banks["instances"]), 34: ("buffer", p0.banks["assets"])})
            entries.update(p0.vegetation_entries())
            self.sets[path] = d.uniform_set(program, 0, entries)

    def _ensure_discovery_set(self) -> None:
        b = self.b
        resources = ["bank_static_nodes", "bank_static_leaf_ids", "bank_static_leaf_instances", "bank_roots",
                     "bank_instances", "bank_nodes", b["scene0"], b["scene1"], b["frontier1"], b["args"],
                     b["camera"], b["scene_info"], b["scene_nodes"], b["scene_bounds"], b["instance_scene"],
                     b["admission_nodes"], b["owed"], "bank_figure_bindings", b["retry_dispatch"]]
        if self.mask & 5:
            raise PlanError("offline_live_source_set1_not_modelled")
        discover = PATHS["DISCOVER_PATH"]
        self.sets[discover] = self.d.uniform_set(self.programs[discover], 0,
                                                 {i: ("buffer", name) for i, name in enumerate(resources)})

    def _ensure_native_set(self) -> None:
        b = self.b
        resources = [b["candidates"], b["args"], b["native_control"], b["native_rows"], b["native_placements"]]
        resources += [f"lang_{field}" for field in ("glyphs", "idioms", "runs", "run_bindings", "languages",
                                                    "representatives")]
        resources += [b["native_plans"], b["native_plan_control"], b["admission_nodes"], b["native_coverage"],
                      b["owed"], "lang_part_timings", "lang_fold_rules"]
        entries = {}
        for binding, name in enumerate(resources):
            entries[binding if binding < 16 else 20 + binding - 16] = ("buffer", name)
        entries[22] = ("texture", "lang_atlas")
        native = PATHS["NATIVE_PATH"]
        self.sets[native] = self.d.uniform_set(self.programs[native], 0, entries)

    def _set_keys(self, path: str) -> dict:
        return {0: self.sets[path]}

    # ---------------------------------------------------------- dispatch helpers
    def _dims(self, count: int, local: int = 64) -> tuple:
        if count <= 0:
            return (0, 1, 1)
        groups = (count - 1) // local + 1
        x = min(groups, self.groups_x)
        return (x, (groups - 1) // self.groups_x + 1, 1)

    def _retry(self, job: int) -> tuple:
        return (self.b["retry_dispatch"], (4 + job * 3) * 4)

    def _host_count(self, path: str, push: list, count: int, label: str) -> None:
        if count == 0:
            return
        program = self.programs[path]
        if self.recording_cut:
            job = 0 if count == 1 else (1 if count == self.caps["candidates"] else 2)
            self.d.dispatch(label, program, self._set_keys(path), i32s(*push), indirect=self._retry(job))
        else:
            self.d.dispatch(label, program, self._set_keys(path), i32s(*push), groups=self._dims(count))

    def _indirect(self, path: str, push: list, slot: int, label: str) -> None:
        self.d.dispatch(label, self.programs[path], self._set_keys(path), i32s(*push),
                        indirect=(self.b["args"], slot * 16))

    def _owed(self, stage: int, generation: int, label: str) -> None:
        rows = self.caps["native_rows"]
        columns = math.ceil(self.page[0] / self.lang["full_advance"])
        origin = self.lang["native_origin"]
        push = i32s(stage, rows, columns, self.caps["native_span"]) + f32s(self.page[0], self.page[1], *origin)
        push += i32s(self.viewport[0], self.viewport[1], 3, 3)  # HudText.RESERVED_ROWS
        push += i32s(self.caps["sites"], generation, self._i32(self.groups_x), 0)
        path = OWED[stage]
        name = f"P1 owed[{stage}] {label}"
        if stage in (1, 24, 25, 26):
            self.d.dispatch(name, self.programs[path], self._set_keys(path), push, indirect=(self.b["owed"], 32))
            return
        cell_stages = (0, 2, 3, 9, 11, 12, 13, 14, 15, 20, 21, 22)
        count = rows if stage == 4 else rows * columns if stage in cell_stages else 1
        if self.recording_cut:
            job = 4 if stage == 4 else 5 if stage in cell_stages else 0
            self.d.dispatch(name, self.programs[path], self._set_keys(path), push, indirect=self._retry(job))
        else:
            self.d.dispatch(name, self.programs[path], self._set_keys(path), push,
                            groups=self._dims(count, 1 if stage == 4 else 64))

    def _native(self, round_index: int, final_round: int, stages: list, label: str) -> None:
        lang = self.lang
        native = PATHS["NATIVE_PATH"]
        program = self.programs[native]
        sets = self._set_keys(native)

        def push(stage):
            return i32s(stage, self.caps["native_rows"], lang["glyph_count"], lang["idiom_count"], lang["run_count"],
                        lang["depiction_span_count"], self.caps["sites"], self.epoch) \
                + f32s(self.page[0], self.page[1], *lang["native_origin"]) \
                + i32s(lang["half_advance"], lang["full_advance"], self._i32(self.groups_x), self.caps["native_span"]) \
                + i32s(self.caps["admission_nodes"], lang["crest_idiom"], round_index, final_round)

        def once(stage, name):
            if self.recording_cut:
                self.d.dispatch(name, program, sets, push(stage), indirect=self._retry(0))
            else:
                self.d.dispatch(name, program, sets, push(stage), groups=(1, 1, 1))
        for stage in stages:
            name = f"P1 native[{stage}] {label}"
            if stage in (2, 4):
                once(8, name + " pack")
                self.d.dispatch(name + " rows", program, sets, push(9), indirect=(self.b["native_control"], 0))
            if stage == 0:
                if self.recording_cut:
                    self.d.dispatch(name, program, sets, push(0), indirect=self._retry(3))
                else:
                    self.d.dispatch(name, program, sets, push(0), groups=self._dims(self.caps["native_rows"]))
            elif stage in (1, 6, 7):
                self.d.dispatch(name, program, sets, push(stage), indirect=(self.b["args"], PASSES["SLOT_SCENE1"] * 16))
            elif stage in (2, 4):
                self.d.dispatch(name, program, sets, push(stage), indirect=(self.b["native_control"], 0))
            elif stage in (3, 5):
                once(stage, name)
            if stage == 2:
                for binding_stage in (10, 11, 12, 13):
                    sub = f"P1 native[{binding_stage}] bindings {label}"
                    if binding_stage in (10, 12):
                        self.d.dispatch(sub, program, sets, push(binding_stage),
                                        indirect=(self.b["args"], PASSES["SLOT_SCENE1"] * 16))
                    else:
                        once(binding_stage, sub)

    def _witness(self, stage: int, level: int, revision: int, label: str) -> None:
        slot = PASSES["SLOT_FRONTIER"] + level
        self._indirect(TRAVERSE[stage], [stage, level, 0, revision], slot, f"P1 witness[{stage}] level{level} {label}")
        self._indirect(TRAVERSE[33], [stage, level, 1, revision], slot, f"P1 witness lanes[{stage}] level{level} {label}")

    # ---------------------------------------------------------- execute
    def camera_bytes(self, camera: dict) -> bytes:
        out = bytearray(PASSES["CAMERA_BYTES"])
        o = 0
        for matrix in (camera["world_to_clip"], world.mat_inverse(camera["world_to_clip"])):
            for column in matrix:
                struct.pack_into("<4f", out, o, *column)
                o += 16
        eye, forward = camera["origin"], camera["forward"]
        floats = [eye[0], eye[1], eye[2], 0.0, forward[0], forward[1], forward[2], 0.0,
                  self.page[0], self.page[1], float(self.lang["full_advance"]), float(self.lang["row_pitch"]),
                  1.0 / 1024.0, 0.0, 0.0, 0.0]
        struct.pack_into("<16f", out, o, *floats)
        o += 64
        struct.pack_into("<I", out, 156, self.caps["admission_nodes"])
        struct.pack_into("<2I", out, 184, *self.phase)
        facts = self.t["facts"]
        c = self.caps
        struct.pack_into("<12I", out, o, facts["instance_count"], self.epoch, facts["max_level"], self.groups_x,
                         c["frontier"], c["leaves"], c["admitted"], c["crossings"], c["segments"], c["candidates"],
                         c["sites"], c["slot_map"])
        struct.pack_into("<2f", out, 240, *self.lang["native_origin"])
        struct.pack_into("<I", out, 248, facts["figure_binding_words"])
        return bytes(out)

    def execute(self, camera: dict, prepass_size: tuple) -> None:
        d, b = self.d, self.b
        facts = self.t["facts"]
        self.epoch += 1
        camera_bytes = bytearray(self.camera_bytes(camera))
        struct.pack_into("<I", camera_bytes, 180, 0)  # P0 water span count: water withdrawn
        scene_info = bytearray(self._pack_scene_info())
        struct.pack_into("<f", scene_info, 16, facts["hierarchy"]["static_max_displacement"])
        d.upload(b["camera"], 0, bytes(camera_bytes))
        d.upload(b["scene_info"], 0, bytes(scene_info))
        d.upload(b["args"], 0, self.args_template)
        d.upload(b["native_plan_control"], 0, i32s(0, 1, 1, 0, 0, 0, 0, 0))
        revision = facts["change_serial"]
        pixels = prepass_size[0] * prepass_size[1]
        generation = facts["hierarchy"]["generation"]
        shadow = PATHS["SHADOW_PATH"]
        self._host_count(shadow, [0, 0, 0, revision], pixels, "P1 shadow[0] receivers")
        self._host_count(shadow, [1, 0, 0, revision], self.caps["candidates"], "P1 shadow[1] windows")
        self._host_count(shadow, [2, 0, 0, revision], 1, "P1 shadow[2] prefix")
        for stage in (0, 1, 24, 25, 26, 11, 20, 21, 22, 12, 13, 14, 15, 2, 8):
            self._owed(stage, generation, "backstop")
        self._native(0, 2, [0, 7, 4, 5, 6, 0, 1, 2], "shadow compaction")
        self._owed(9, generation, "shadow")
        self._owed(10, generation, "credited")
        for stage in range(4):
            self._host_count(TRAVERSE[11], [11, stage, pixels, revision], pixels, f"P2 surface[{stage}]")
        levels = facts["max_level"] + 1
        rounds = 3
        for cut_round in range(rounds):
            self._host_count(TRAVERSE[20], [20, self.caps["candidates"], pixels, self.caps["native_rows"]], 1,
                             f"P1 schedule round{cut_round}")
            self.recording_cut = True
            self._cut_round(levels, revision, cut_round, rounds - 1, generation)
            self.recording_cut = False
        self._host_count(TRAVERSE[21], [21, 0, 0, revision], 1, "P1 finish")
        self._host_count(shadow, [3, 0, 0, revision], 1, "P1 shadow[3] seal")
        d.dispatch("P4 sites[1] native plans", self.programs[PATHS["SITES_PATH"]], self._set_keys(PATHS["SITES_PATH"]),
                   i32s(1, 0, 0, revision), indirect=(b["native_plan_control"], 0))
        self._indirect(PATHS["VISIBILITY_PATH"], [0, 0, 0, revision], PASSES["SLOT_CANDIDATES"], "P3 visibility")
        self._indirect(EXTRACT[9], [9, 0, 0, revision], PASSES["SLOT_CROSSINGS"], "P2 extract history")
        for level in range(levels):
            self._indirect(TRAVERSE[14], [14, level, 0, revision], PASSES["SLOT_FRONTIER"] + level, f"P1 commit level{level}")
        self._host_count(TRAVERSE[7], [7, 0, 0, revision], 1, "P1 revision")

    def _cut_round(self, levels: int, revision: int, cut_round: int, final_round: int, generation: int) -> None:
        r = f"round{cut_round}"
        self._owed(7, generation, r)
        self._host_count(TRAVERSE[19], [19, 0, 0, revision], 1, f"P1 reset {r}")
        discover = PATHS["DISCOVER_PATH"]
        self._host_count(discover, [0, 0, 0, 0], 1, f"P1 discover[0] {r}")
        front = 0
        for level in range(self.scene_levels):
            self._host_count(discover, [1, front, 0, 0], 1, f"P1 discover[1] level{level} {r}")
            self._indirect(discover, [2, front, 0, 0], PASSES["SLOT_SCENE0"] + front, f"P1 discover[2] level{level} {r}")
            front = 1 - front
        self._host_count(discover, [3, front, 0, 0], 1, f"P1 discover[3] {r}")
        for digit in range(16):
            self._host_count(discover, [4, 0, digit, 0], 1, f"P1 discover[4] digit{digit} {r}")
        self._indirect(discover, [5, 0, 0, 0], PASSES["SLOT_ROOTS"], f"P1 discover[5] roots {r}")
        self._host_count(TRAVERSE[12], [12, 0, 0, revision], 1, f"P1 roots {r}")
        for level in range(levels):
            slot = PASSES["SLOT_FRONTIER"] + level
            for kernel in (10, 40, 41, 42, 43):
                self._indirect(TRAVERSE[kernel], [kernel, level, 0, revision], slot, f"P1 explore[{kernel}] level{level} {r}")
            self._indirect(TRAVERSE[30], [10, level, 0, revision], slot, f"P1 explore prepare level{level} {r}")
            self._indirect(TRAVERSE[31], [10, level, 0, revision], slot, f"P1 explore open level{level} {r}")
        for level in range(levels - 1, -1, -1):
            self._indirect(TRAVERSE[8], [8, level, 0, revision], PASSES["SLOT_FRONTIER"] + level, f"P1 bottom_up level{level} {r}")
        self._host_count(TRAVERSE[13], [13, 0, 0, revision], 1, f"P1 budget {r}")
        for level in range(levels):
            slot = PASSES["SLOT_FRONTIER"] + level
            self._indirect(TRAVERSE[9], [9, level, 0, revision], slot, f"P1 top_down level{level} {r}")
            if level == 0:
                self._indirect(TRAVERSE[22], [22, level, 0, revision], slot, f"P1 scene witness {r}")
                self._indirect(TRAVERSE[24], [24, level, 0, revision], PASSES["SLOT_ROOTS"], f"P1 scene queries {r}")
                self._indirect(TRAVERSE[36], [36, level, 0, revision], PASSES["SLOT_ROOTS"], f"P1 scene surface preflight {r}")
                self._indirect(TRAVERSE[29], [29, level, 0, revision], PASSES["SLOT_ROOTS"], f"P1 scene elected {r}")
                self._witness(26, level, revision, r)
                self._indirect(TRAVERSE[25], [25, level, 0, revision], slot, f"P1 scene summaries {r}")
            self._indirect(TRAVERSE[35], [35, level, 0, revision], slot, f"P1 surface preflight level{level} {r}")
            self._indirect(TRAVERSE[23], [23, level, 0, revision], slot, f"P1 witness level{level} {r}")
            self._indirect(TRAVERSE[28], [28, level, 0, revision], slot, f"P1 elected level{level} {r}")
            self._witness(27, level, revision, r)
        self._host_count(TRAVERSE[6], [6, 0, 0, revision], 1, f"P1 reserve {r}")
        for kernel in (2, 37, 38, 39):
            self._indirect(TRAVERSE[kernel], [kernel, 0, 0, revision], PASSES["SLOT_LEAF_GROUPS"], f"P1 demand[{kernel}] {r}")
        self._host_count(TRAVERSE[3], [3, 0, 0, revision], 1, f"P1 seal {r}")
        self._indirect(TRAVERSE[4], [4, 0, 0, revision], PASSES["SLOT_LEAF_GROUPS"], f"P1 admit {r}")
        self._indirect(EXTRACT[0], [0, 0, 0, revision], PASSES["SLOT_ADMITTED"], f"P2 extract edges {r}")
        self._indirect(EXTRACT[1], [1, 0, 0, revision], PASSES["SLOT_CROSSINGS"], f"P2 extract link {r}")
        self._indirect(EXTRACT[2], [2, 0, 0, revision], PASSES["SLOT_CROSSINGS"], f"P2 extract init {r}")
        self._host_count(EXTRACT[3], [3, 0, 0, revision], 1, f"P2 extract rounds {r}")
        rounds = 0
        while rounds < PASSES["MAX_ROUNDS"] and (1 << rounds) < self.caps["crossings"]:
            rounds += 1
        for index in range(rounds):
            self._indirect(EXTRACT[4], [4, index, 0, revision], PASSES["SLOT_ROUNDS"] + index, f"P2 extract min{index} {r}")
        self._indirect(EXTRACT[5], [5, 0, 0, revision], PASSES["SLOT_CROSSINGS"], f"P2 extract cut {r}")
        for index in range(rounds):
            self._indirect(EXTRACT[6], [6, index, 0, revision], PASSES["SLOT_ROUNDS"] + index, f"P2 extract prefix{index} {r}")
        for operation in (8, 7):
            self._indirect(EXTRACT[operation], [operation, 0, 0, revision], PASSES["SLOT_CROSSINGS"],
                           f"P2 extract[{operation}] {r}")
        self._indirect(PATHS["SITES_PATH"], [0, 0, 0, revision], PASSES["SLOT_SEGMENTS"], f"P4 sites {r}")
        self._native(cut_round, final_round, [0, 1, 2, 3], f"certificate {r}")
        self._owed(3, generation, r)
        self._owed(5, generation, r)
        self._native(cut_round, final_round, [0, 1, 2], f"recheck {r}")
        self._owed(4, generation, r)
        self._owed(6, generation, r)
        self._native(cut_round, final_round, [3], f"publish {r}")
        for level in range(levels - 1, -1, -1):
            self._indirect(TRAVERSE[16], [16, level, 0, revision], PASSES["SLOT_FRONTIER"] + level, f"P1 pressure level{level} {r}")
        self._indirect(TRAVERSE[17], [17, 0, 0, revision], PASSES["SLOT_FRONTIER"], f"P1 scene pressure {r}")


# ===================================================================== P5

class Layout:
    """TrueFFFeatureRenderer P5 layout and P6 glyph paint."""

    def __init__(self, device: Device, language: dict, passes: Passes, extent: tuple,
                 viewport: tuple, matlib: dict):
        d = self.d = device
        self.lang = language
        self.extent = extent
        self.viewport = viewport
        sites = passes.caps["sites"]
        self.sites = sites
        self.rows_cap = extent[1] // RENDERER["ROW_PITCH"] + 2
        self.instances_cap = self.rows_cap * (extent[0] // max(language["minimum_advance"], 1) + 1)
        header = RENDERER["CONTROL_HEADER_WORDS"]
        self.control_bytes = (header + 3 * self.rows_cap) * 4
        d.buffer("p5_control", self.control_bytes, indirect=True)
        d.buffer("p5_row_sites", max(sites * 4, 16))
        d.buffer("p5_site_slots", max(sites * 8, 16))
        d.buffer("p5_nodes", max(sites * RENDERER["NODE_BYTES"], 16))
        d.buffer("p5_face_index", (2 + sites * RENDERER["FACE_INDEX_WORDS"]) * 4)
        d.buffer("p5_instances", self.instances_cap * RENDERER["INSTANCE_BYTES"])
        d.buffer("p5_row_instances", self.instances_cap * RENDERER["INSTANCE_BYTES"])
        d.buffer("p5_paint_sites", sites * RENDERER["SITE_BYTES"])
        d.buffer("p5_coverage_camera", RENDERER["COVERAGE_CAMERA_BYTES"], uniform=True)
        slots = 6
        hud_entries = extent[0] // max(language["minimum_advance"], 1) + 1
        d.buffer("p5_hud", (slots + slots * hud_entries) * RENDERER["HUD_ENTRY_BYTES"])
        self.program = d.program("layout", "render/ascii/features/true_ff_feature_layout.glsl")
        b = passes.b
        buffers = ["p5_paint_sites", "lang_languages", b["args"], "lang_runs", "lang_idioms", "lang_glyphs",
                   "p5_control", "p5_row_sites", "p5_site_slots", "p5_nodes", "p5_row_instances"]
        entries = {i: ("buffer", name) for i, name in enumerate(buffers)}
        entries.update({11: ("buffer", "p5_hud"), 12: ("buffer", b["sites"]), 13: ("texture", "p0_depth"),
                        14: ("texture", "p0_ids"), 22: ("texture", "p0_primitive"),
                        15: ("buffer", "p5_coverage_camera"), 17: ("buffer", "lang_run_bindings"),
                        18: ("buffer", "lang_representatives"), 20: ("buffer", "lang_part_timings"),
                        21: ("buffer", "lang_fold_rules"), 23: ("buffer", "p5_face_index"),
                        24: ("buffer", "p5_instances"), 25: ("texture", "lang_atlas")})
        self.set = d.uniform_set(self.program, 0, entries)
        d.buffer("frame_params", STRUCTURE["PARAMS_BYTES"], uniform=True)
        d.buffer("paper_facts", 64, uniform=True)
        d.texture("matlib", matlib["width"], matlib["height"], "rgba8", matlib["rgba8"])
        d.texture("page_target", viewport[0], viewport[1], "rgba16f")
        d.texture("frame_target", viewport[0], viewport[1], "rgba16f")
        paper_columns = math.ceil(extent[0] / language["full_advance"])
        paper_rows = math.ceil(extent[1] / language["row_pitch"])
        d.buffer("paper_cells", paper_columns * paper_rows * 4)
        self.paper_program = d.program("page", "render/ascii/composition/true_ff_page_background.glsl")
        self.paper_set = d.uniform_set(self.paper_program, 0, {
            0: ("texture", "page_target"), 1: ("buffer", "frame_params"),
            2: ("buffer", "paper_cells"),
        })
        self.paper_groups = (math.ceil(paper_columns / 8), math.ceil(paper_rows / 8), 1)
        self.glyph_program = d.program("glyphs", "render/ascii/features/true_ff_feature_glyphs.glsl")
        glyph_entries = {
            1: ("buffer", "frame_params"), 2: ("texture", "frame_target"),
            6: ("texture", "matlib"), 7: ("buffer", "p0_heightfield"),
            8: ("texture", "terrain_elevations"), 9: ("texture", "terrain_heights"),
            10: ("texture", "terrain_materials"), 11: ("buffer", "p5_paint_sites"),
            12: ("buffer", "p5_instances"), 13: ("buffer", "lang_glyphs"),
            14: ("texture", "lang_atlas"), 15: ("buffer", "p5_control"),
            16: ("buffer", "paper_facts"),
        }
        self.glyph_set = d.uniform_set(self.glyph_program, 0, glyph_entries)
        d.dumps.extend(("page_target", "frame_target"))
        self.passes = passes

    def render(self, camera: dict) -> None:
        d, lang = self.d, self.lang
        d.upload("p5_hud", 0, bytes(6 * RENDERER["HUD_ENTRY_BYTES"]), "P5 hud snapshot (no lines)")
        rows = min(math.ceil(self.extent[1] / RENDERER["ROW_PITCH"]), self.rows_cap)
        inverse = world.mat_inverse(camera["world_to_clip"])
        coverage = b"".join(f32s(*column) for column in inverse)
        coverage += f32s(*camera["origin"], 0.0, *camera["forward"], 0.0)
        d.upload("p5_coverage_camera", 0, coverage)
        d.clear("p5_control", 0, self.control_bytes)
        count_word = PASSES["SITES_ARGS_OFFSET"] // 4 + 3 if "SITES_ARGS_OFFSET" in PASSES \
            else (PASSES["SLOT_SITES"] * 16) // 4 + 3
        overflow_word = PASSES["SLOT_META"] * 4 + 1
        groups = Passes._i32(GROUPS_MAX)

        def push(stage):
            return i32s(stage, rows, lang["run_count"], count_word, self.sites, self.instances_cap,
                        self.extent[0], self.extent[1]) \
                + f32s(0.0, 0.0, float(lang["glyph_count"]), float(lang["depiction_span_count"])) \
                + i32s(lang["half_advance"], lang["full_advance"], groups, lang["idiom_count"]) \
                + i32s(self.viewport[0], self.viewport[1], 3, 3) \
                + i32s(overflow_word, lang["crest_idiom"], 0, 0)
        R = RENDERER
        names = {R["STAGE_ARGS"]: "args", R["STAGE_COPY"]: "copy", R["STAGE_BIN"]: "bin",
                 R["STAGE_OFFSETS"]: "offsets", R["STAGE_SCATTER"]: "scatter", R["STAGE_LAYOUT"]: "layout",
                 R["STAGE_COVERAGE"]: "coverage", R["STAGE_FACE"]: "face", R["STAGE_HUD"]: "hud",
                 R["STAGE_FINAL"]: "final", R["STAGE_COMPACT"]: "compact"}
        for stage in (R["STAGE_ARGS"], R["STAGE_COPY"], R["STAGE_BIN"], R["STAGE_OFFSETS"], R["STAGE_SCATTER"],
                      R["STAGE_LAYOUT"], R["STAGE_COVERAGE"], R["STAGE_FACE"], R["STAGE_HUD"], R["STAGE_FINAL"],
                      R["STAGE_COMPACT"]):
            label = f"P5 layout[{names[stage]}]"
            # CONTROL_*_ARGS are byte offsets into the control buffer.
            if stage in (R["STAGE_COPY"], R["STAGE_BIN"], R["STAGE_SCATTER"]):
                d.dispatch(label, self.program, {0: self.set}, push(stage), indirect=("p5_control", R["CONTROL_SITE_ARGS"]))
            elif stage in (R["STAGE_LAYOUT"], R["STAGE_FACE"], R["STAGE_COMPACT"]):
                d.dispatch(label, self.program, {0: self.set}, push(stage), indirect=("p5_control", R["CONTROL_LAYOUT_ARGS"]))
            elif stage == R["STAGE_COVERAGE"]:
                d.dispatch(label, self.program, {0: self.set}, push(stage), indirect=("p5_control", R["CONTROL_COVERAGE_ARGS"]))
            else:
                d.dispatch(label, self.program, {0: self.set}, push(stage), groups=(1, 1, 1))

    def paint(self, camera: dict, sun: tuple, epoch: int) -> None:
        self.d.upload("frame_params", 0, pack_frame_params(camera, sun, epoch), "P6 frame params")
        facts = pack_atmosphere_facts(sun, self.lang)
        self.d.upload("paper_facts", 0, facts, "P6 paper facts")
        self.d.dispatch("page background", self.paper_program, {0: self.paper_set}, facts,
                        groups=self.paper_groups)
        for phase, name in ((0, "paper"), (1, "ink")):
            push = i32s(self.instances_cap, self.extent[0], self.extent[1], 1,
                        phase, 0, 0, 0)
            self.d.dispatch(f"P6 glyphs[{name}]", self.glyph_program, {0: self.glyph_set}, push,
                            indirect=("p5_control", RENDERER["CONTROL_GLYPH_ARGS"]))


# ===================================================================== driver

def executor() -> Path:
    digest = hashlib.sha256(EXECUTOR_SOURCE.read_bytes()).hexdigest()[:16]
    out = RUN_DIR / "bin" / f"true_ff_offline_metal-{digest}"
    if not out.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        built = subprocess.run(["xcrun", "swiftc", "-O", str(EXECUTOR_SOURCE), "-o", str(out)],
                               capture_output=True, text=True)
        if built.returncode != 0:
            raise PlanError("offline_metal_executor_build_failed: " + built.stderr[-1500:])
    return out


def canonical_pose() -> dict:
    registry = json.loads((ROOT / "config/godot_proof_pose_registry.json").read_text(encoding="utf-8"))

    def find(node):
        if isinstance(node, dict):
            if "canonical_capture_recipe" in node and "camera_positions" in node["canonical_capture_recipe"]:
                return node["canonical_capture_recipe"]
            for value in node.values():
                found = find(value)
                if found:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = find(value)
                if found:
                    return found
        return None
    recipe = find(registry)
    if recipe is None:
        raise PlanError("canonical_airplane_recipe_missing")
    return recipe


def build(work: Path, viewport=(1280, 720)) -> tuple:
    recipe = canonical_pose()
    camera = world.camera_facts(recipe["camera_positions"][0], recipe["camera_yaw_radians"],
                                recipe["camera_pitch_radians"], recipe["perspective_fov_degrees"], viewport,
                                frame_time=float(recipe.get("product_time", 0.0)))
    extent = camera["page_extent"]
    prepass_size = ((extent[0] + 1) // 2, (extent[1] + 1) // 2)  # TrueFFFrameRenderer.PREPASS_DIVISOR
    terrain = world.terrain_world()
    language = world.language_catalog()
    matlib = world.material_library()
    sun = world.sun_direction((float(recipe.get("product_time", 12.0)) / 24.0) % 1.0)
    d = Device(work)
    # TrueFFFeatureBank publication (storage) and the TrueFFFeatureRenderer catalog.
    for name, data in terrain["buffers"].items():
        d.buffer(f"bank_{name}", max(len(data), 4), data, uniform=name == "terrain_params")
    for name in ("glyphs", "idioms", "runs", "run_bindings", "representatives", "languages",
                 "part_timings", "fold_rules"):
        d.buffer(f"lang_{name}", max(len(language[name]), 16), language[name])
    d.texture("lang_atlas", language["atlas_size"][0], language["atlas_size"][1], "rgba8", language["atlas_rgba8"])
    p0 = PrePass(d, terrain, prepass_size)
    passes = Passes(d, terrain, language, mask=0)
    passes.prepare(extent, viewport, prepass_size, p0)
    layout = Layout(d, language, passes, extent, viewport, matlib)
    # One frame: TrueFFFrameRenderer.render_frame order.
    epoch = 1
    p0.prepare(revision=1)
    p0.draw(camera, epoch)
    light = p0.draw_light(camera, sun, epoch)
    if not light.get("valid"):
        raise PlanError("shadow_light_view_invalid:" + str(light.get("reason")))
    passes.execute(camera, prepass_size)
    layout.render(camera)
    layout.paint(camera, sun, epoch)
    meta_offset = PASSES["SLOT_META"] * 16
    d.watch = [["meta", passes.b["args"], meta_offset, 4],
               ["certificate", passes.b["args"], 236 * 4, 4],
               ["p5", "p5_control", 0, RENDERER["CONTROL_HEADER_WORDS"]],
               ["native", passes.b["native_control"], 0, 8],
               ["owed", passes.b["owed"], 0, 8],
               ["retry", passes.b["retry_dispatch"], 0, 2]]
    receipt = {"epoch": epoch,
               "camera": {"position": recipe["camera_positions"][0], "yaw": recipe["camera_yaw_radians"],
                          "pitch": recipe["camera_pitch_radians"], "fov": recipe["perspective_fov_degrees"],
                          "viewport": list(viewport), "page_extent": list(extent)},
               "terrain_bank": terrain["digest"], "terrain_dims": list(terrain["dims"]),
               "family_mask": 0, "candidates": passes.caps["candidates"], "programs": len(d.programs),
               "commands": len(d.commands), "sun": list(sun),
               "not_modelled": ["static placements", "actors", "rigid movers", "vegetation runtime",
                                "live water/effect/sky families (withdrawn variant used)", "HUD lines"]}
    return d, receipt


def analyse(trace: list, expected_epoch: int) -> dict:
    first_diagnostic = None
    first_overflow = None
    for row in trace:
        meta = row.get("meta")
        if not meta:
            continue
        # META.y (P5 copies it as "upstream") reports item, stage and pending
        # omissions. It is diagnostic, not page-fatal; the production shaders
        # keep certified survivors and P5 keeps a deterministic row prefix.
        if first_diagnostic is None and meta[1] != 0:
            first_diagnostic = row
        if first_overflow is None and meta[3] & (1 << 31):
            first_overflow = row
    # Native row solve: control word 4 is its typed failure; word 3 rows,
    # 5 placed, 6 total proposals (TrueFFFeatureRenderer._on_gpu_status).
    native_failures = [row for row in trace if row.get("native") and row["native"][4]
                       and "native[" in row.get("label", "")]
    last = trace[-1] if trace else {}
    p5 = last.get("p5", [0] * 20)
    meta = last.get("meta", [0, 0, 0, 0])
    certificate = last.get("certificate")
    shadow_plan_missing = not certificate or len(certificate) < 2 \
        or certificate[1] != (0x80000000 | expected_epoch)
    return {"first_diagnostic": first_diagnostic, "first_overflow": first_overflow,
            "shadow_plan_missing": shadow_plan_missing, "meta": meta, "p5": p5,
            "native_failures": native_failures, "certificate": last.get("certificate"),
            "sites": p5[3], "glyphs": p5[11], "upstream": p5[12], "layout": p5[14]}


def bits(value: int, table: dict) -> list:
    return [name for bit, name in table.items() if value & bit] + \
        (["producer_capacity_or_contract"] if table is ADMISSION_ERRORS and value & FATAL_MASK else [])


def painted_pixel_count(path: Path, viewport: tuple) -> int:
    """Count RGBA16F target pixels P6 actually wrote (alpha becomes nonzero)."""
    expected = int(viewport[0]) * int(viewport[1]) * 8
    if not path.is_file():
        return -1
    data = path.read_bytes()
    if len(data) != expected:
        return -1
    return sum(data[offset + 6:offset + 8] != b"\0\0" for offset in range(0, len(data), 8))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--project", type=Path)
    args = parser.parse_args(argv)
    if args.project:
        global PROJ
        shaders.glsl.PROJ = args.project.resolve()
    stamp = time.strftime("%Y%m%dT%H%M%S")
    work = args.out or RUN_DIR / stamp
    work.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        device, receipt = build(work)
    except (PlanError, world.PublicationError, shaders.ShaderError) as exc:
        print(f"TRUE_FF_OFFLINE_FRAME FAIL plan: {exc}")
        print("TRUE_FF_OFFLINE_FRAME_RESULT " + json.dumps({"ok": False, "stage": "host_replica", "reason": str(exc),
                                                             "work": str(work)}))
        return 1
    plan_path = work / "plan.json"
    plan_path.write_text(json.dumps(device.plan()), encoding="utf-8")
    run = subprocess.run([str(executor()), str(plan_path), str(work / "out")], capture_output=True, text=True)
    trace_path = work / "out" / "trace.jsonl"
    trace = [json.loads(line) for line in trace_path.read_text().splitlines()] if trace_path.is_file() else []
    seconds = round(time.time() - started, 1)
    if run.returncode != 0:
        last = trace[-1] if trace else {}
        reason = last.get("status", "EXECUTOR_FAILED") if run.returncode == 3 else run.stderr.strip()[-800:]
        print(f"TRUE_FF_OFFLINE_FRAME FAIL gpu: {reason} at command {last.get('index')} {last.get('label')}"
              f" {last.get('error', '')}")
        print("TRUE_FF_OFFLINE_FRAME_RESULT " + json.dumps({"ok": False, "stage": "gpu", "reason": reason,
                                                             "command": last.get("label"), "work": str(work),
                                                             "seconds": seconds}))
        return 1
    verdict = analyse(trace, int(receipt["epoch"]))
    painted_pixels = painted_pixel_count(work / "out" / "frame_target.bin",
                                         tuple(receipt["camera"]["viewport"]))
    paper_pixels = painted_pixel_count(work / "out" / "page_target.bin",
                                       tuple(receipt["camera"]["viewport"]))
    problems = []
    if verdict["first_overflow"]:
        row = verdict["first_overflow"]
        problems.append(f"capacity overflow META.w bit31 set by command {row['index']} '{row['label']}'")
    if verdict["shadow_plan_missing"]:
        certificate = verdict["certificate"] or [0, 0]
        problems.append(f"missing current-frame shadow plan: word=0x{certificate[1]:08x}")
    if verdict["glyphs"] == 0:
        problems.append(f"zero painted cells: P5 placed 0 glyphs (sites={verdict['sites']} "
                        f"upstream=0x{verdict['upstream']:08x} layout=0x{verdict['layout']:08x})")
    if painted_pixels <= 0:
        problems.append("P6 glyph paint wrote no target pixels" if painted_pixels == 0
                        else "P6 glyph paint target dump missing or malformed")
    if paper_pixels <= 0:
        problems.append("page background wrote no target pixels" if paper_pixels == 0
                        else "page background target dump missing or malformed")
    result = {"ok": not problems, "problems": problems, "sites": verdict["sites"], "glyphs": verdict["glyphs"],
              "painted_pixels": painted_pixels, "paper_pixels": paper_pixels,
              "meta": verdict["meta"], "p5_upstream": verdict["upstream"], "p5_layout": verdict["layout"],
              "seconds": seconds, "work": str(work), **receipt}
    for problem in problems:
        print("TRUE_FF_OFFLINE_FRAME FAIL " + problem)
    if not problems:
        print(f"TRUE_FF_OFFLINE_FRAME OK sites={verdict['sites']} glyphs={verdict['glyphs']} "
              f"painted_pixels={painted_pixels} paper_pixels={paper_pixels} "
              f"commands={len(trace)} seconds={seconds}")
    (work / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print("TRUE_FF_OFFLINE_FRAME_RESULT " + json.dumps(result if args.json else
                                                        {k: result[k] for k in ("ok", "sites", "glyphs", "meta", "seconds", "work")}))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
