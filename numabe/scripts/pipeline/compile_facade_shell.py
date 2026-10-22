#!/usr/bin/env python3
"""Generate presentation-only facade shells for OSM buildings.

FL-4787 facade lane.

WHAT THIS SOLVES
================
Buildings are baked into the 2.5D terrain heightfield, which has no vertical
faces: a building reads as a coloured plateau, never as a wall with windows.
This emits a low-poly wall shell per building so the vertical surface exists as
real geometry, while the heightfield keeps owning gameplay.

    terrain heightfield  = collision / walkability authority   (unchanged)
    facade shell         = presentation authority               (this file)

COLLISION SAFETY (the load-bearing constraint)
==============================================
``engine/physics.cpp:663`` drops a triangle from the physics soup when any of
its vertex alphas exceeds 128::

    if (colors[3] > 128 || colors[7] > 128 || colors[11] > 128) return;

``HitFace`` repeats the same test (``engine/world_internal.h:275-283``), and the
renderer never reads alpha at all. So every vertex here is written with
**alpha = 255**: the shell renders and is guaranteed not to contribute
collision, walkability, or hit geometry. The existing collidable building assets
(e.g. ``assets/meshes/Administration.akm``) use alpha 0 -- do not copy that.

WHY VERTEX COLOUR AND NOT A TEXTURE
===================================
AKM is ASCII PLY and the loader maps ``red``/``green``/``blue``/``alpha`` into
``Vert::rgba`` (``engine/world_mesh.cpp:195-198``); ``s``/``t`` are explicitly
skipped (``:202``), so there is no UV path. ``RenderFace`` interpolates vertex
RGB and sets ``spare |= 0x8`` (``engine/render/render_world_pass.cpp:3288-3302``),
which makes the resolver take the ``use_auto_mat`` branch and bypass the
material palette entirely (``render_resolve.cpp:2578-2661``). Per-vertex colour
is therefore the only way to get a grey wall with blue windows, and it needs no
format or runtime change.

Crisp bands come from emitting two rings per band: consecutive bands share a Z
but carry different colours, so the boundary does not smear under interpolation.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from bake_osm_building_footprints import (  # noqa: E402
    _align_footprint_to_instance,
    _parse_footprint,
)

from scripts.pipeline import geo_frames as GF

HEIGHT_SCALE = 16.0
ALPHA_NO_COLLISION = 255

# FL-4809: the terminal-safe 6x6x6 colour cube.
#
# Classic renders into CP437 cells. A colour ON these levels needs no
# dithering; a colour OFF them is dithered across neighbouring cells, and at
# cell resolution that dither is what destroys a brand read -- a FamilyMart
# fascia stops being "green band, white field, blue band" and becomes noise.
#
# Emitters were choosing near-miss colours everywhere and nothing caught it:
# the place-front mesh for FamilyMart carried 8 distinct colours and not one
# sat on the cube (brand green (0,146,79), brand blue (0,145,211), body
# (21,112,160), ...), and all 11 entries of lower_plan_fixtures.VARIANT_COLOURS
# were off it too. Fixing each emitter would leave the next one free to
# reintroduce the bug, so the snap happens HERE, at the single writer every
# Classic mesh passes through. An emitter may still express intent in exact
# RGB; this is the boundary that makes intent renderable.
SAFE_LEVELS = (0, 51, 102, 153, 204, 255)
_SAFE_SNAP = tuple(min(SAFE_LEVELS, key=lambda level: abs(level - value))
                   for value in range(256))


def _srgb_to_lab(rgb):
    """sRGB (0-255) -> CIELAB (D65); used only to choose the perceptually nearest cube colour."""
    def lin(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(float(v)) for v in rgb)
    x = (0.4124564 * r + 0.3575761 * g + 0.1804375 * b) / 0.95047
    y = 0.2126729 * r + 0.7151522 * g + 0.0721750 * b
    z = (0.0193339 * r + 0.1191920 * g + 0.9503041 * b) / 1.08883
    f = lambda t: t ** (1.0 / 3.0) if t > 216.0 / 24389.0 else (24389.0 / 27.0 * t + 16.0) / 116.0
    fx, fy, fz = f(x), f(y), f(z)
    return 116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)


_SAFE_CUBE = tuple((r, g, b) for r in SAFE_LEVELS for g in SAFE_LEVELS for b in SAFE_LEVELS)
_SAFE_CUBE_LAB = tuple(_srgb_to_lab(c) for c in _SAFE_CUBE)
_SAFE_MEMO: dict = {}


def _building_paint(wall_rgb, roof_rgb, entry):
    """A building's own plain paint (palette wall/roof plus facade-profile wall/trim/roof): kept exact by write_akm."""
    prof = (entry or {}).get("facade_profile") or {}
    return frozenset(tuple(int(c) for c in rgb) for rgb in
                     (wall_rgb, roof_rgb, prof.get("wall_rgb"), prof.get("trim_rgb"), prof.get("roof_rgb")) if rgb)


def quantise_safe_cube(rgb):
    """Snap an RGB triple onto the terminal-safe cube (alpha is never touched).

    The snap is the CIELAB-nearest of the 216 cube colours. Independent
    per-channel rounding moved channels in opposite directions and changed hue:
    hand-sampled Numabe cream walls (232,222,211) / (238,220,205) became pink
    (255,204,204) and rendered pink in headed Classic (build_reg_63, FL-4876).
    """
    key = tuple(int(c) for c in rgb)
    hit = _SAFE_MEMO.get(key)
    if hit is None:
        t = _srgb_to_lab(key)
        hit = _SAFE_CUBE[min(range(len(_SAFE_CUBE)),
                             key=lambda i: sum((p - q) ** 2 for p, q in zip(_SAFE_CUBE_LAB[i], t)))]
        _SAFE_MEMO[key] = hit
    return hit

DEFAULT_STOREY_M = 3.6
# Fraction of a storey occupied by glazing, measured from the storey floor.
WINDOW_BAND = (0.32, 0.86)

STATION_PORTAL_RASTER = (32, 24)
STATION_SIGN_RASTER = (48, 16)


def _centroid(poly: list[tuple[float, float]]) -> tuple[float, float]:
    return (sum(p[0] for p in poly) / len(poly),
            sum(p[1] for p in poly) / len(poly))


def _dedupe_ring(poly: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for p in poly:
        if not out or abs(p[0] - out[-1][0]) > 1e-6 or abs(p[1] - out[-1][1]) > 1e-6:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) < 1e-6 and abs(out[0][1] - out[-1][1]) < 1e-6:
        out.pop()
    return out


def _mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def build_bands(roof_m: float, wall_rgb, glass_rgb, roof_rgb,
                storey_m: float = DEFAULT_STOREY_M) -> list[tuple[float, float, tuple]]:
    """Return [(z0_m, z1_m, rgb)] bands from ground to the roof."""
    bands: list[tuple[float, float, tuple]] = []
    n = max(1, int(round(roof_m / storey_m)))
    h = roof_m / n
    # Ground floor reads darker: entrances, plinth, shadowed recess.
    plinth = _mix(wall_rgb, (0, 0, 0), 0.35)
    for i in range(n):
        z0 = i * h
        w0, w1 = WINDOW_BAND
        base_rgb = plinth if i == 0 else wall_rgb
        bands.append((z0, z0 + h * w0, base_rgb))
        # Ground floor glazing is the entrance band: darker glass.
        g = _mix(glass_rgb, (0, 0, 0), 0.25) if i == 0 else glass_rgb
        bands.append((z0 + h * w0, z0 + h * w1, g))
        bands.append((z0 + h * w1, z0 + h, wall_rgb))
    # Parapet cap, slightly lighter than the wall so the roofline reads.
    cap = _mix(wall_rgb, roof_rgb, 0.5)
    bands.append((roof_m, roof_m + min(0.6, roof_m * 0.04), cap))
    return bands


def write_akm(
    path: Path,
    verts: list[tuple],
    faces: list[list[int]],
    *,
    comment: str = "FL-4787 facade shell - presentation only, alpha=255 => no collision",
    exact_rgb=frozenset(),
) -> None:
    """Write only geometry accepted by Classic's ASCII PLY loader.

    ``exact_rgb``: colours written unsnapped. Only a building's own plain wall /
    roof / trim paint is passed here (facade shells and gameplay masses). The
    6-level cube cannot express cream at all -- every hand-assessed cream,
    greige and ivory Numabe wall collapsed to the same (204,204,204) in headed
    Classic (build_reg_64) -- while ASCIIID Classic draws exact mesh RGB as flat
    true colour (dither_probe.py, FL-4876). FL-4809's reason for the cube,
    brand fascias and signage, still passes through the snap.
    """
    exact_rgb = frozenset(tuple(int(c) for c in rgb) for rgb in exact_rgb)
    if path.suffix.lower() != ".akm":
        raise ValueError(f"Classic mesh output must end in .akm: {path}")
    if not verts or not faces:
        raise ValueError(f"Classic mesh must contain vertices and faces: {path}")
    for index, vertex in enumerate(verts):
        if len(vertex) != 10:
            raise ValueError(
                f"AKM vertex {index} has {len(vertex)} columns, expected 10")
        if not all(math.isfinite(float(value)) for value in vertex[:6]):
            raise ValueError(f"AKM vertex {index} has non-finite geometry")
        if any(int(value) != value or not 0 <= int(value) <= 255
               for value in vertex[6:]):
            raise ValueError(f"AKM vertex {index} has invalid RGBA channels")
    for index, face in enumerate(faces):
        if len(face) not in (2, 3, 4):
            raise ValueError(
                f"AKM face {index} has {len(face)} vertices; Classic accepts "
                "only lines, triangles, and quads")
        if len(set(face)) != len(face):
            raise ValueError(f"AKM face {index} repeats a vertex index")
        if any(not isinstance(value, int) or not 0 <= value < len(verts)
               for value in face):
            raise ValueError(f"AKM face {index} references an invalid vertex")
    lines = [
        "ply",
        "format ascii 1.0",
        f"comment {comment}",
        f"element vertex {len(verts)}",
        "property float x", "property float y", "property float z",
        "property float nx", "property float ny", "property float nz",
        "property uchar red", "property uchar green",
        "property uchar blue", "property uchar alpha",
        f"element face {len(faces)}",
        "property list uchar uint vertex_indices",
        "end_header",
    ]
    for (x, y, z, nx, ny, nz, r, g, b, a) in verts:
        # Snap to the terminal-safe cube on the way out. Alpha is untouched:
        # it carries the collision contract, not colour.
        if (int(r), int(g), int(b)) not in exact_rgb:
            r, g, b = quantise_safe_cube((r, g, b))
        lines.append(f"{x:.3f} {y:.3f} {z:.3f} {nx:.2f} {ny:.2f} {nz:.2f} {r} {g} {b} {a}")
    for f in faces:
        lines.append(f"{len(f)} " + " ".join(str(i) for i in f))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pixel_panel(extent: dict[str, float], pixels, *, content_scale: float,
                 ground_z_m: float, normal_offset_m: float = 0.0):
    """Build one crisp, per-cell-coloured panel in facade-local X/Z.

    ``pixels`` is an RGB array. Every cell gets four vertices so adjacent
    source colours never interpolate across a boundary. This uses Classic's
    existing vertex-colour path and adds neither UVs nor a runtime format.
    """
    height, width = pixels.shape[:2]
    u0, u1 = float(extent["u_min"]), float(extent["u_max"])
    z0 = float(extent["z_min"]) - ground_z_m
    z1 = float(extent["z_max"]) - ground_z_m
    verts: list[tuple] = []
    faces: list[list[int]] = []
    for row in range(height):
        top = z1 - (z1 - z0) * row / height
        bottom = z1 - (z1 - z0) * (row + 1) / height
        for col in range(width):
            left = u0 + (u1 - u0) * col / width
            right = u0 + (u1 - u0) * (col + 1) / width
            r, g, b = (int(value) for value in pixels[row, col])
            base = len(verts)
            colour = (r, g, b, ALPHA_NO_COLLISION)
            verts.extend([
                (left * content_scale, normal_offset_m * content_scale,
                 bottom * content_scale, 0.0, 1.0, 0.0, *colour),
                (right * content_scale, normal_offset_m * content_scale,
                 bottom * content_scale, 0.0, 1.0, 0.0, *colour),
                (right * content_scale, normal_offset_m * content_scale,
                 top * content_scale, 0.0, 1.0, 0.0, *colour),
                (left * content_scale, normal_offset_m * content_scale,
                 top * content_scale, 0.0, 1.0, 0.0, *colour),
            ])
            faces.append([base, base + 3, base + 2, base + 1])
    return verts, faces


def _station_ps1_front(portal_extent: dict[str, float], portal_pixels,
                       sign_extent: dict[str, float], sign_pixels, *,
                       content_scale: float, ground_z_m: float):
    """Author a readable station frontage from source colour evidence.

    A full detector crop lowered as a 32x24 wall became a dark, floating photo
    card in Classic.  Preserve source-derived colour and the real sign panel,
    but express the entrance as large PS1 architectural regions that can sit
    inside the station portal/canopy assembly.
    """
    import numpy as np

    height = float(portal_extent["z_max"]) - float(portal_extent["z_min"])
    width = float(portal_extent["u_max"]) - float(portal_extent["u_min"])
    u0 = float(portal_extent["u_min"])
    z0 = float(portal_extent["z_min"])

    pixels = portal_pixels.astype(np.float32)
    luminance = pixels.mean(axis=2)
    bright = pixels[luminance >= np.percentile(luminance, 62)]
    dark = pixels[luminance <= np.percentile(luminance, 38)]
    wall_rgb = tuple(int(v) for v in np.median(bright, axis=0))
    glass_rgb = tuple(int(v) for v in np.median(dark, axis=0))
    trim_rgb = tuple(int(round((wall_rgb[i] * .65) + 48 * .35)) for i in range(3))

    def solid(extent, rgb, offset):
        block = np.array([[rgb]], dtype=np.uint8)
        return _pixel_panel(extent, block, content_scale=content_scale,
                            ground_z_m=ground_z_m, normal_offset_m=offset)

    regions = [
        (portal_extent, wall_rgb, 0.0),
        ({"u_min": u0 + width * .12, "u_max": u0 + width * .88,
          "z_min": z0 + height * .04, "z_max": z0 + height * .57},
         glass_rgb, .018),
        ({"u_min": u0 + width * .44, "u_max": u0 + width * .58,
          "z_min": z0 + height * .03, "z_max": z0 + height * .57},
         tuple(max(18, v - 18) for v in glass_rgb), .030),
        ({"u_min": u0 + width * .10, "u_max": u0 + width * .90,
          "z_min": z0 + height * .58, "z_max": z0 + height * .64},
         trim_rgb, .022),
    ]
    verts: list[tuple] = []
    faces: list[list[int]] = []
    for extent, rgb, offset in regions:
        region_verts, region_faces = solid(extent, rgb, offset)
        base = len(verts)
        verts.extend(region_verts)
        faces.extend([[base + index for index in face] for face in region_faces])
    sign_verts, sign_faces = _pixel_panel(
        sign_extent, sign_pixels, content_scale=content_scale,
        ground_z_m=ground_z_m, normal_offset_m=.045,
    )
    base = len(verts)
    verts.extend(sign_verts)
    faces.extend([[base + index for index in face] for face in sign_faces])
    return verts, faces, {
        "wall_rgb": list(wall_rgb),
        "glass_rgb": list(glass_rgb),
        "trim_rgb": list(trim_rgb),
        "mode": "source_palette_ps1_station_structure",
    }


def _fused_crop(observation_ids: list[str], object_observations: dict,
                capture_index: dict, bundle_root: Path,
                output_size: tuple[int, int]):
    """Median-fuse detector crops, then apply deterministic PS1 quantization."""
    import cv2
    import numpy as np

    observations = {
        row["observation_id"]: row for row in object_observations["observations"]
    }
    captures = {row["capture_id"]: row for row in capture_index["captures"]}
    crops = []
    source_ids = []
    for observation_id in observation_ids:
        row = observations[observation_id]
        capture = captures[row["capture_id"]]
        image_path = bundle_root / capture["local_path"]
        image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"cannot read facade source image: {image_path}")
        x0, y0, x1, y1 = [int(round(value)) for value in row["bbox_xyxy_px"]]
        x0, x1 = max(0, x0), min(image.shape[1], x1)
        y0, y1 = max(0, y0), min(image.shape[0], y1)
        if x1 <= x0 or y1 <= y0:
            raise ValueError(f"empty facade source crop: {observation_id}")
        crop = cv2.resize(
            image[y0:y1, x0:x1], output_size, interpolation=cv2.INTER_AREA
        )
        crops.append(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
        source_ids.append(observation_id)
    if not crops:
        raise ValueError("facade representation has no source crops")
    fused = np.median(np.stack(crops).astype(np.float32), axis=0)
    fused = np.clip(np.floor((fused + 4.0) / 8.0) * 8.0, 0, 255).astype(np.uint8)
    return fused, source_ids


def _load_a3d_edit():
    path = REPO_ROOT / "docs" / "agent" / "cli-anything" / "a3d_edit.py"
    spec = importlib.util.spec_from_file_location("fl4814_a3d_edit", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lower_anchored_world_ir_facade(
    *, world_ir_path: Path, reconstruction_path: Path,
    camera_receipt_path: Path,
    terrain_metadata_path: Path, map_path: Path,
    meshes_out: Path, lowered_world_ir_out: Path, receipt_out: Path,
    station_plan_path: Path,
) -> dict[str, Any]:
    """Lower one accepted anchored facade/sign slice into Classic artifacts."""
    from camera_solve.geodesy import EnuFrame
    import geo_frames as GF
    from probe_instance_terrain_z import load_height_grid, terrain_z
    from world_ir import WorldIRStore, schema as S

    store = WorldIRStore.load(world_ir_path)
    store.assert_valid()
    reconstruction = json.loads(reconstruction_path.read_text(encoding="utf-8"))
    if not reconstruction.get("gate", {}).get("pass"):
        raise ValueError("anchored facade reconstruction gate did not pass")
    camera_receipt = json.loads(camera_receipt_path.read_text(encoding="utf-8"))
    terrain_metadata = json.loads(terrain_metadata_path.read_text(encoding="utf-8"))
    from scripts.pipeline.compile_station_facade_plan import (
        require_station_plan,
    )
    station_plan = json.loads(
        station_plan_path.read_text(encoding="utf-8"))
    station_placement = require_station_plan(station_plan)
    if (station_plan["inputs"]["world_ir_sha256"] != _sha256(world_ir_path)
            or station_plan["inputs"]["reconstruction_sha256"]
            != _sha256(reconstruction_path)):
        raise ValueError("station facade plan provenance differs from lowering inputs")

    facade = next(store.features_of_class("facade_plane"))
    sign = next(store.features_of_class("sign"))
    representation = station_plan.get("representation") or {}
    selected_template = representation.get("selected_template")
    pack_chain = representation.get("locale_pack_chain")
    if not isinstance(selected_template, dict) or not isinstance(
            pack_chain, list):
        raise ValueError(
            "station facade plan lacks its selected representation contract")

    portal_extent = reconstruction["portal"]["extent_uv_m"]
    facade_slug = facade["feature_id"].replace(":", "_").replace("/", "_")
    sign_slug = sign["feature_id"].replace(":", "_").replace("/", "_")
    asset = station_plan.get("asset") or {}
    mesh_name = str(asset.get("mesh_name") or "")
    if not mesh_name:
        raise ValueError("station facade plan has no preauthored asset")
    instance_name = f"facade_{facade_slug}"
    mesh_path = meshes_out / mesh_name
    if (str(mesh_path.resolve()) != str(asset.get("mesh_path"))
            or not mesh_path.is_file()
            or _sha256(mesh_path) != asset.get("mesh_sha256")):
        raise ValueError("station facade asset differs from preassembly plan")
    source_observations = asset.get("source_observations") or {}
    portal_sources = list(source_observations.get("facade") or [])
    sign_sources = list(source_observations.get("sign") or [])

    enu_origin = camera_receipt["corridor"]["enu_origin_wgs84"]
    enu_frame = EnuFrame(
        enu_origin["longitude"], enu_origin["latitude"],
        enu_origin["ellipsoidal_height_m"],
    )
    plane_origin = reconstruction["plane"]["origin_enu_m"]
    tangent = reconstruction["plane"]["horizontal_tangent_enu"]
    normal = reconstruction["plane"]["normal_enu"]
    planned_attachment = station_placement["attachment"]
    if (planned_attachment["plane_origin_enu_m"] != plane_origin
            or planned_attachment["horizontal_tangent_enu"] != tangent
            or planned_attachment["normal_enu"] != normal):
        raise ValueError("station facade reconstruction drifted after planning")
    outward_offset_m = float(planned_attachment.get("outward_offset_m") or 0.0)
    # FL-4876 STATION-RAIL-01: authored slide along the plane tangent (metres,
    # signed), applied after the outward offset; absent/zero keeps the old pose.
    lateral_shift_m = float(planned_attachment.get("lateral_shift_m") or 0.0)
    effective_origin = [
        float(plane_origin[0]) + float(normal[0]) * outward_offset_m
        + float(tangent[0]) * lateral_shift_m,
        float(plane_origin[1]) + float(normal[1]) * outward_offset_m
        + float(tangent[1]) * lateral_shift_m,
        float(plane_origin[2]) + float(normal[2]) * outward_offset_m
        + float(tangent[2]) * lateral_shift_m,
    ]
    lon, lat, _alt = enu_frame.to_geodetic(*effective_origin)
    easting, northing = GF.utm_forward(lon, lat)
    world_x = (
        (easting - float(terrain_metadata["origin_easting"]))
        * float(terrain_metadata["content_scale"])
        + float(terrain_metadata["offset_x"])
    )
    world_y = (
        (northing - float(terrain_metadata["origin_northing"]))
        * float(terrain_metadata["content_scale"])
        + float(terrain_metadata["offset_y"])
    )
    ground_bake = terrain_z(load_height_grid(map_path), world_x, world_y)
    if ground_bake is None:
        raise ValueError("station facade anchor is outside the compiled terrain")
    transform = [
        float(tangent[0]), float(tangent[1]), 0.0, 0.0,
        float(normal[0]), float(normal[1]), 0.0, 0.0,
        0.0, 0.0, HEIGHT_SCALE, 0.0,
        world_x, world_y, float(ground_bake), 1.0,
    ]
    editor = _load_a3d_edit()
    instance = editor.a3d_format.A3DInstance(
        mesh_name=mesh_name, inst_name=instance_name,
        transform=transform, flags=3, story_id=-1,
    )
    pre, version, instances, player_start, enemy_gens, markers = (
        editor.read_a3d_sections(map_path)
    )
    editor.write_a3d_sections(
        map_path, pre, version, [*instances, instance], player_start, enemy_gens, markers
    )

    store.add_representation(S.generated_representation(
        representation_id=f"rep:{facade_slug}:classic_vertex_colour",
        feature_id=facade["feature_id"], target="mesh_asset",
        product={"kind": "akm_shell", "mesh_name": mesh_name,
                 "vertex_alpha": ALPHA_NO_COLLISION,
                 "instance_name": instance.inst_name},
        lowering={"compiler": "scripts/pipeline/compile_facade_shell.py",
                  "mode": "source_palette_ps1_station_structure",
                  "collision": "none; alpha=255 drops every triangle"},
        authority_claim=S.AUTHORITY_PRESENTATION_ONLY,
    ))
    store.add_representation(S.generated_representation(
        representation_id=f"rep:{sign_slug}:classic_vertex_colour",
        feature_id=sign["feature_id"], target="mesh_asset",
        product={"kind": "embedded_vertex_colour_panel", "mesh_name": mesh_name,
                 "template_id": selected_template["template_id"],
                 "template_pack": selected_template["pack_id"]},
        lowering={"compiler": "scripts/pipeline/compile_facade_shell.py",
                  "face_mode": "source_evidence_pixel_panel",
                  "template_face_mode": selected_template["face_mode"],
                  "template_application": "geometry family and jurisdiction identity",
                  "source_text": sign["properties"]["text"]["value"]},
        authority_claim=S.AUTHORITY_PRESENTATION_ONLY,
    ))
    store.assert_valid()
    store.save(lowered_world_ir_out)

    _pre, _version, actual_instances, _ps, _gens, _markers = (
        editor.read_a3d_sections(map_path)
    )
    matches = [row for row in actual_instances if row.mesh_name == mesh_name]
    if len(matches) != 1 or not mesh_path.is_file() or mesh_path.stat().st_size == 0:
        raise ValueError("station facade artifact attestation failed")
    anchor_ref = str(reconstruction.get("anchor", {}).get("osm_ref") or "")
    if not anchor_ref:
        raise ValueError("station facade reconstruction carries no canonical OSM anchor")
    station_instances_path = receipt_out.parent / "station_facade_instances.json"
    station_instances = [{
        "placement_id": station_placement["placement_id"],
        "attachment_id": (station_placement.get("attachment") or {}).get(
            "attachment_id"),
        "feature_id": f"feature:station_entrance:{anchor_ref}",
        "facade_feature_id": facade["feature_id"],
        "mesh_name": mesh_name,
        "inst_name": instance_name,
        "transform": transform,
        "station_plan_schema": station_plan["schema"],
        "flags": 3,
        "story_id": -1,
        "semantic_family": "station_facade",
        "intended_width_m": round(
            float(portal_extent["u_max"]) - float(portal_extent["u_min"]), 6),
        "intended_depth_m": 0.045,
        "intended_height_m": round(
            float(portal_extent["z_max"]) - float(portal_extent["z_min"]), 6),
        "planned_tangent": [float(tangent[0]), float(tangent[1])],
        "planned_normal": [float(normal[0]), float(normal[1])],
        "authored_spatial_decision": station_placement.get(
            "authored_spatial_decision"),
        "spatial_basis": (
            "anchored_facade_reconstruction.anchor.osm_ref+plane"
            f"+outward_normal_offset:{outward_offset_m:.3f}m"
            f"+lateral_shift_along_tangent:{lateral_shift_m:.3f}m"),
    }]
    station_instances_path.write_text(
        json.dumps(station_instances, ensure_ascii=False, indent=2,
                   sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "schema": "y92.classic_facade_lowering.v1",
        "stage": "executed_artifact_attested",
        "inputs": {
            "world_ir": {"path": str(world_ir_path.resolve()),
                         "sha256": _sha256(world_ir_path)},
            "reconstruction": {"path": str(reconstruction_path.resolve()),
                               "sha256": _sha256(reconstruction_path)},
        },
        "selected_template": selected_template,
        "locale_pack_chain": list(pack_chain),
        "source_observations": {"facade": portal_sources, "sign": sign_sources},
        "representation": {
            "mesh_path": str(mesh_path.resolve()), "mesh_sha256": _sha256(mesh_path),
            "instance_name": instance.inst_name,
            "instances_path": str(station_instances_path.resolve()),
            "instances_sha256": _sha256(station_instances_path),
            "world_position": [world_x, world_y, ground_bake],
            "outward_normal_offset_m": outward_offset_m,
            "lateral_shift_along_tangent_m": lateral_shift_m,
            "facade_raster": list(asset["facade_raster"]),
            "sign_raster": list(asset["sign_raster"]),
            "vertices": int(asset["vertices"]),
            "faces": int(asset["faces"]),
            "alpha": int(asset["alpha"]),
            "authored_palette": asset["authored_palette"],
        },
        "artifact_inspection": {
            "a3d_instance_matches": len(matches),
            "akm_nonempty": mesh_path.stat().st_size > 0,
            "lowered_world_ir_representations": len(store.representations),
        },
        "counts": {
            "facades_emitted": 1, "signs_emitted": 1,
            "mesh_instances_embedded": len(matches),
            "selected_locale_templates": 1,
        },
    }
    receipt_out.parent.mkdir(parents=True, exist_ok=True)
    receipt_out.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def build_shell(poly_world, roof_m, wall_rgb, glass_rgb, roof_rgb, content_scale,
                storey_m: float = DEFAULT_STOREY_M, profile: dict | None = None):
    """Return (verts, faces, origin) for one building's wall shell.

    Local Z is in WORLD UNITS above local ground, so the instance transform can
    keep the existing row2 scale of 16 (world units -> bake units) and only its
    translation Z needs to carry the terrain datum.
    """
    ring = _dedupe_ring(poly_world)
    if len(ring) < 3:
        return None
    ox, oy = _centroid(ring)
    local = [(x - ox, y - oy) for x, y in ring]
    n = len(local)

    if profile is not None:
        # FL-4814: compile a whole parent face before adding small CV details.
        # These discrete bays are synthesized presentation, not surveyed windows
        # or traversable entrances. A partition replaces overlays to avoid z-fight.
        vertices, faces = [], []
        wall = tuple(profile['wall_rgb'])
        glass = tuple(profile['glass_rgb'])
        trim = tuple(profile['trim_rgb'])
        roof = tuple(profile['roof_rgb'])
        floors = max(1, round(roof_m / float(profile['storey_m'])))
        floor_h = roof_m / floors
        lengths = [math.dist(local[i], local[(i + 1) % n]) for i in range(n)]
        entrance_edge = max(range(n), key=lambda i: lengths[i])
        area = sum(local[i][0] * local[(i + 1) % n][1]
                   - local[(i + 1) % n][0] * local[i][1] for i in range(n))
        for edge, length in enumerate(lengths):
            if length < 1e-6:
                continue
            x0, y0 = local[edge]
            x1, y1 = local[(edge + 1) % n]
            sign = 1 if area >= 0 else -1
            nx, ny = sign * (y1-y0)/length, -sign * (x1-x0)/length
            def panel(u0, u1, z0, z1, rgb):
                first = len(vertices)
                for u, z in ((u0,z0), (u1,z0), (u1,z1), (u0,z1)):
                    # FL-4814: presentation epsilon only; mass containment owns ramps.
                    epsilon = 0.01 * content_scale
                    vertices.append((x0+(x1-x0)*u+nx*epsilon, y0+(y1-y0)*u+ny*epsilon,
                                     z*content_scale, nx, ny, 0.0,
                                     *rgb, ALPHA_NO_COLLISION))
                face = [first, first+1, first+2, first+3]
                faces.append(face if area >= 0 else list(reversed(face)))
            bays = max(1, int(length / content_scale / profile['bay_width_m']))
            for floor in range(floors):
                bottom, top = floor*floor_h, (floor+1)*floor_h
                for bay in range(bays):
                    left, right = bay/bays, (bay+1)/bays
                    margin = (1-profile['window_fraction'])/(2*bays)
                    wl, wr = left+margin, right-margin
                    door = edge == entrance_edge and bay == 0 and floor == 0
                    low = bottom if door else bottom+0.32*floor_h
                    high = min(top-0.12, bottom+(min(2.1, floor_h*0.86) if door else floor_h*0.80))
                    panel(left, wl, bottom, top, wall)
                    panel(wr, right, bottom, top, wall)
                    if low > bottom:
                        panel(wl, wr, bottom, low, wall)
                    panel(wl, wr, high, top, wall)
                    panel(wl, wr, low, high, trim if door else glass)
            panel(0, 1, roof_m, roof_m+min(0.25, roof_m*.025), roof)
        return vertices, faces, (ox, oy)

    # Outward normal per edge, for flat-ish wall lighting.
    normals = []
    for i in range(n):
        x0, y0 = local[i]
        x1, y1 = local[(i + 1) % n]
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy) or 1.0
        normals.append((dy / ln, -dx / ln))

    bands = build_bands(roof_m, wall_rgb, glass_rgb, roof_rgb, storey_m)

    verts: list[tuple] = []
    faces: list[list[int]] = []
    for (z0_m, z1_m, rgb) in bands:
        z0 = z0_m * content_scale
        z1 = z1_m * content_scale
        base = len(verts)
        for i in range(n):
            x, y = local[i]
            nx, ny = normals[i]
            verts.append((x, y, z0, nx, ny, 0.0, rgb[0], rgb[1], rgb[2], ALPHA_NO_COLLISION))
        for i in range(n):
            x, y = local[i]
            nx, ny = normals[i]
            verts.append((x, y, z1, nx, ny, 0.0, rgb[0], rgb[1], rgb[2], ALPHA_NO_COLLISION))
        for i in range(n):
            j = (i + 1) % n
            faces.append([base + i, base + j, base + n + j, base + n + i])
    return verts, faces, (ox, oy)


def compile_procedural_facade_shells(
    *, building_specs_path: Path, palette_path: Path, map_path: Path,
    meshes_out: Path, instances_out: Path, summary_path: Path | None = None,
    roof_field_dir: Path | None = None, content_scale: float = GF.WORLD_UNITS_PER_METRE,
    storey_m: float = DEFAULT_STOREY_M, embed: bool = True,
    ground_map_path: Path | None = None,
) -> dict[str, Any]:
    """Compile the existing facade-shell fallback for every accounted building.

    FL-4813: these shells make uncovered buildings visible while remaining
    separately counted from street-derived facade observations.

    A measured roof field still wins when one is supplied.  World-IR direct
    builds may instead carry the already-audited ``roof_height_m`` hierarchy
    in their building specs.  Either source controls only shell height; the
    repeated storey/window bands remain explicit procedural presentation.
    """
    from probe_instance_terrain_z import load_height_grid, terrain_z

    palette = json.loads(palette_path.read_text(encoding="utf-8"))
    materials = palette["materials"]
    building_materials = palette["building_material"]
    specs = json.loads(building_specs_path.read_text(encoding="utf-8"))
    # FL-4814: the assembled heightfield includes roof plateaus. Sampling its
    # building centre would place the entire facade above its own roof.
    grid = load_height_grid(ground_map_path or map_path)
    meshes_out.mkdir(parents=True, exist_ok=True)

    instances: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    height_sources: dict[str, int] = {}
    for spec in specs:
        source = str(spec.get("source") or "")
        entry = building_materials.get(source)
        if not entry:
            skipped.append({"source": source or str(spec.get("inst_name")),
                            "reason": "no palette entry"})
            continue

        roof_m = None
        height_method = str(spec.get("height_method") or "")
        height_provenance = str(spec.get("height_provenance_class") or "unknown")
        if roof_field_dir is not None:
            roof_field_path = roof_field_dir / (source.replace("/", "_") + ".roof_field.json")
            if roof_field_path.is_file():
                roof_field = json.loads(roof_field_path.read_text(encoding="utf-8"))
                regions = roof_field.get("roof_regions") or []
                if regions:
                    dominant = max(regions, key=lambda row: row.get("vertex_fraction") or 0.0)
                    roof_m = float(dominant["elevation_m_above_ground"])
                    height_method = "dominant measured roof-field tier"
                    height_provenance = "measured"
        if roof_m is None and spec.get("roof_height_m") is not None:
            roof_m = float(spec["roof_height_m"])
        if roof_m is None or roof_m <= 0.5:
            skipped.append({"source": source, "reason": "no usable roof height"})
            continue

        wall_rgb = tuple(materials[str(entry["wall"])]["bg"])
        glass_rgb = tuple(materials[str(entry["wall"])]["fg"])
        roof_rgb = tuple(materials[str(entry["roof"])]["bg"])
        polygon = _align_footprint_to_instance(_parse_footprint(spec), spec)
        built = build_shell(
            polygon, roof_m, wall_rgb, glass_rgb, roof_rgb,
            content_scale, storey_m, profile=entry.get('facade_profile'),
        )
        if built is None:
            skipped.append({"source": source, "reason": "degenerate footprint"})
            continue
        vertices, faces, (origin_x, origin_y) = built
        ground = terrain_z(grid, origin_x, origin_y)
        if ground is None:
            skipped.append({"source": source, "reason": "origin off map"})
            continue

        mesh_name = f"facade_{source.replace('/', '_')}.akm"
        mesh_path = meshes_out / mesh_name
        write_akm(mesh_path, vertices, faces, exact_rgb=_building_paint(wall_rgb, roof_rgb, entry))
        instance_name = f"facade_{spec.get('inst_name') or source}"
        instances.append({
            "feature_id": spec.get('feature_id'),
            "source": source,
            "mesh_name": mesh_name,
            "inst_name": instance_name,
            "transform": [
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, HEIGHT_SCALE, 0.0,
                float(origin_x), float(origin_y), float(round(ground, 3)), 1.0,
            ],
            "flags": 3,
            "story_id": -1,
            "facade_profile": entry.get('facade_profile'),
            "provenance": {
                "facade_structure": ("synthesized locale discrete bays"
                    if entry.get('facade_profile') else "synthesized procedural fallback bands"),
                "footprint": "derived from World IR OSM building footprint",
                "height": height_provenance,
                "height_method": height_method,
                "colour": ("synthesized locale/source-neighbourhood palette assignment"
                    if entry.get('facade_profile') else materials[str(entry["wall"])]["provenance_class"]),
                "collision": "none; alpha=255 drops every triangle at physics.cpp:663",
            },
        })
        height_sources[height_provenance] = height_sources.get(height_provenance, 0) + 1

    instances_out.parent.mkdir(parents=True, exist_ok=True)
    instances_out.write_text(
        json.dumps(instances, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    embedded = 0
    if embed:
        editor = _load_a3d_edit()
        pre, version, existing, player_start, enemy_gens, markers = editor.read_a3d_sections(map_path)
        existing_names = {row.inst_name for row in existing}
        duplicates = sorted(row["inst_name"] for row in instances if row["inst_name"] in existing_names)
        if duplicates:
            raise ValueError(f"facade instances already embedded: {duplicates[:4]}")
        additions = [editor.a3d_format.A3DInstance(
            mesh_name=row["mesh_name"], inst_name=row["inst_name"],
            transform=row["transform"], flags=row["flags"], story_id=row["story_id"],
        ) for row in instances]
        editor.write_a3d_sections(
            map_path, pre, version, [*existing, *additions],
            player_start, enemy_gens, markers,
        )
        _pre, _version, actual, _start, _gens, _markers = editor.read_a3d_sections(map_path)
        actual_names = {row.inst_name for row in actual}
        missing = sorted(row["inst_name"] for row in instances if row["inst_name"] not in actual_names)
        if missing:
            raise ValueError(f"facade artifact attestation missed instances: {missing[:4]}")
        embedded = len(instances)

    missing_meshes = [
        row["mesh_name"] for row in instances
        if not (meshes_out / row["mesh_name"]).is_file()
        or (meshes_out / row["mesh_name"]).stat().st_size == 0
    ]
    if missing_meshes:
        raise ValueError(f"facade artifact attestation missed meshes: {missing_meshes[:4]}")
    receipt = {
        "schema": "y92.procedural_facade_fallback.v1",
        "stage": "executed_artifact_attested",
        "counts": {
            "building_specs": len(specs),
            "facades_procedural_fallback": len(instances),
            "facade_meshes_nonempty": len(instances) - len(missing_meshes),
            "facade_instances_embedded": embedded,
            "skipped": len(skipped),
        },
        "height_provenance": height_sources,
        "instances": str(instances_out.resolve()),
        "meshes_out": str(meshes_out.resolve()),
        "skipped": skipped,
    }
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--building-specs", type=Path, required=True)
    ap.add_argument("--palette", type=Path, required=True)
    ap.add_argument("--roof-field-dir", type=Path, required=True)
    ap.add_argument("--map", type=Path, required=True,
                    help="baked map, used to read the terrain datum under each building")
    ap.add_argument("--meshes-out", type=Path, required=True, help="directory for the .akm shells")
    ap.add_argument("--instances-out", type=Path, required=True)
    ap.add_argument("--content-scale", type=float, default=GF.WORLD_UNITS_PER_METRE)
    ap.add_argument("--storey-m", type=float, default=DEFAULT_STOREY_M)
    ap.add_argument("--summary", type=Path)
    args = ap.parse_args(argv)

    from probe_instance_terrain_z import load_height_grid, terrain_z

    palette = json.loads(args.palette.read_text())
    mats = palette["materials"]
    bmat = palette["building_material"]
    specs = json.loads(args.building_specs.read_text())
    grid = load_height_grid(args.map)

    args.meshes_out.mkdir(parents=True, exist_ok=True)
    instances = []
    made = 0
    skipped = []

    for spec in specs:
        source = spec.get("source") or ""
        entry = bmat.get(source)
        if not entry:
            skipped.append((source or spec.get("inst_name"), "no palette entry"))
            continue

        rf_path = args.roof_field_dir / (source.replace("/", "_") + ".roof_field.json")
        if not rf_path.is_file():
            skipped.append((source, "no measured roof field"))
            continue
        rf = json.loads(rf_path.read_text())
        regions = rf.get("roof_regions") or []
        if not regions:
            skipped.append((source, "roof field has no regions"))
            continue
        # The shell is one prism, so use the tier that covers the most of the
        # roof rather than the peak: a lift overrun must not inflate the whole
        # building.
        dominant = max(regions, key=lambda r: r.get("vertex_fraction") or 0.0)
        roof_m = float(dominant["elevation_m_above_ground"])
        if roof_m <= 0.5:
            skipped.append((source, f"dominant tier only {roof_m:.2f} m"))
            continue

        wall_rgb = tuple(mats[str(entry["wall"])]["bg"])
        glass_rgb = tuple(mats[str(entry["wall"])]["fg"])
        roof_rgb = tuple(mats[str(entry["roof"])]["bg"])

        poly = _align_footprint_to_instance(_parse_footprint(spec), spec)
        built = build_shell(poly, roof_m, wall_rgb, glass_rgb, roof_rgb,
                            args.content_scale, args.storey_m)
        if built is None:
            skipped.append((source, "degenerate footprint"))
            continue
        verts, faces, (ox, oy) = built

        ground = terrain_z(grid, ox, oy)
        if ground is None:
            skipped.append((source, "origin off map"))
            continue

        mesh_name = f"facade_{source.replace('/', '_')}.akm"
        write_akm(args.meshes_out / mesh_name, verts, faces,
                  exact_rgb=_building_paint(wall_rgb, roof_rgb, entry))

        instances.append({
            "mesh_name": mesh_name,
            "inst_name": f"facade_{spec.get('inst_name') or source}",
            "transform": [
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, HEIGHT_SCALE, 0.0,
                float(ox), float(oy), float(round(ground, 3)), 1.0,
            ],
            "flags": 3,
            "story_id": -1,
            "provenance": {
                "geometry": "derived",
                "geometry_rule": "OSM footprint extruded to the dominant measured LiDAR roof tier",
                "colour": mats[str(entry["wall"])]["provenance_class"],
                "collision": "none; alpha=255 drops every triangle at physics.cpp:663",
            },
        })
        made += 1

    args.instances_out.parent.mkdir(parents=True, exist_ok=True)
    args.instances_out.write_text(json.dumps(instances, indent=2) + "\n", encoding="utf-8")

    receipt = {"shells": made, "skipped": skipped,
               "meshes_out": str(args.meshes_out), "instances": str(args.instances_out)}
    if args.summary:
        args.summary.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    print(f"[facade] built {made} shells -> {args.meshes_out}")
    if skipped:
        print(f"[facade] skipped {len(skipped)}: {skipped[:6]}")
    print(f"[facade] wrote {args.instances_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
