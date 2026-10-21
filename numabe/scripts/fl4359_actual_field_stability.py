#!/usr/bin/env python3
"""Compare observed FG/BG at exactly matched retained world samples.

Uses the canonical v16 prepack witnesses at their original quarter-centre
pixels, joined to the same-frame native field. Never differences unregistered
screenshots or calls ordinary camera motion shimmer. This is a sampled colour
instability diagnostic; it does not replace the canonical geometry/jitter gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from fl4359_actual_field import ActualFieldError, ActualFieldReader, TRACE_FIELD_SCHEMA
from fl4359_raster_jitter_capture import stream_frames
from fl4359_cell_dump_raster import AtlasPage
from fl4359_native_frame_artifact import validate_native_frame, publish_native_gif, native_presentation_provenance

COLUMNS = (
    "screen_cell", "subcell_slot", "probe_prepack_valid",
    "probe_prepack_identity", "probe_prepack_world_position",
    "probe_prepack_provenance", "probe_prepack_revisions",
    "presented_surface", "sample_index", "probe_reconstructed_clip_corners",
    "probe_reconstructed_clip_valid", "probe_source_capture_join_flags",
    "probe_actual_vertex_witness_valid", "probe_actual_vertex_witness_available",
    "probe_actual_vertex_witness_enabled", "probe_raster_witness_enabled",
    "probe_actual_vertex_witness_surface",
    "probe_actual_vertex_witness_instance", "probe_actual_vertex_witness_primitive",
    "probe_actual_vertex_clip_corners",
    "probe_raster_contender_overflow", "probe_raster_candidate_source_contender_count",
    "probe_raster_candidate_source_nearest_depth", "probe_raster_candidate_source_farthest_depth",
    "probe_prepack_hardware_depth",
)
THRESHOLD = 1.0 / 255.0 + 1e-6


def runtime_readback_health(path):
    """A finite payload is insufficient when the GPU did not finish its copy."""
    if not path.is_file():
        return {"status": "unavailable", "log": str(path)}
    raw = path.read_bytes()
    timeouts = raw.count(b"ERROR: timeout waiting for fence")
    return {"status": "failed" if timeouts else "clean", "log": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(), "gpu_fence_timeouts": timeouts}


def _native_source_key(field, source, label):
    """Return the exact source tuple used by ``presented_surface.source``.

    ``front_sources`` stores a physical reference.  The presented row stores
    the same reference as ``[logical, face-local texel, generation, layer]``.
    Generation and layer are required for this same-frame lookup, but neither
    is part of the returned content token.  Publication/origin metadata is
    still validated here so an incomplete source record fails closed.
    """
    if field.metadata.get("schema") != TRACE_FIELD_SCHEMA:
        raise ActualFieldError("v2 source schema is required")
    if getattr(field, "projection", "perspective") != "perspective":
        # Ortho references are product indices and carry no source generation.
        raise ActualFieldError("orthographic source has no v2 generation")
    if not isinstance(source, Mapping) or source.get("present") is not True:
        raise ActualFieldError(f"v2 {label} source is absent")
    reference = source.get("reference")
    generation = source.get("capture_generation")
    logical = source.get("logical_probe")
    layer = source.get("layer")
    texel = source.get("texel")
    publication = source.get("publication")
    if (isinstance(reference, bool) or not isinstance(reference, int) or reference <= 0
            or isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0
            or isinstance(logical, bool) or not isinstance(logical, int) or logical < 0
            or isinstance(layer, bool) or not isinstance(layer, int) or layer < 0
            or not isinstance(texel, (list, tuple)) or len(texel) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in texel)
            or not isinstance(publication, Mapping) or not publication
            or not all(isinstance(k, str) and isinstance(v, str) and v
                       for k, v in publication.items())):
        raise ActualFieldError(f"v2 {label} source provenance is incomplete")
    state = field.metadata.get("source_capture_state", {})
    if not isinstance(state, Mapping):
        raise ActualFieldError("v2 source_capture_state is missing")
    extent = state.get("probe_size")
    source_bank = source.get("bank")
    expected_bank = "underlay" if label.startswith("underlay ") else "front"
    kind = source.get("capture_generation_kind")
    geometry = source.get("geometry_capture")
    generation_maps = getattr(field, "source_generation_maps", {})
    if (source_bank != expected_bank or kind not in ("captured_geometry", "generated_sky")
            or geometry is not (kind == "captured_geometry")
            or not isinstance(generation_maps, Mapping) or source_bank not in generation_maps):
        raise ActualFieldError(f"{label} source lacks an exact typed bank witness")
    generation_map = generation_maps[source_bank]
    origins = state.get("captured_origins_after_capture")
    valid_mask = state.get("captured_origin_valid_mask")
    bands = state.get("capture_bands")
    face_area = int(extent) * int(extent) if isinstance(extent, int) and extent > 0 else 0
    address = reference & 0x0fffffff
    if (face_area <= 0 or not isinstance(generation_map, np.ndarray)
            or generation_map.ndim != 3 or generation_map.shape[1:] != (extent, extent)
            or address <= 0 or address > face_area * generation_map.shape[0]):
        raise ActualFieldError(f"v2 {label} source address is outside capture")
    actual_layer, local = divmod(address - 1, face_area)
    expected_texel = [local % int(extent), local // int(extent)]
    token = generation | (0x80000000 if kind == "generated_sky" else 0)
    if (generation > 0x7fffffff or actual_layer != layer or expected_texel != list(texel)
            or int(generation_map[actual_layer, expected_texel[1], expected_texel[0]]) != token):
        raise ActualFieldError(f"v2 {label} source address disagrees with capture metadata")
    if (not isinstance(origins, list) or not isinstance(valid_mask, int)
            or isinstance(valid_mask, bool) or not isinstance(bands, list)
            or logical >= len(origins) or logical >= len(bands)
            or not (valid_mask & (1 << logical))
            or layer // 6 != bands[logical]):
        raise ActualFieldError(f"v2 {label} source has no valid captured origin")
    origin = origins[logical]
    if (not isinstance(origin, (list, tuple)) or len(origin) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       and math.isfinite(float(v)) for v in origin)):
        raise ActualFieldError(f"v2 {label} captured origin is invalid")
    face_local = (layer % 6) * face_area + local
    return (source_bank, logical, face_local, token, layer)


def _content_token(identity, world, revisions, label, source_key=None):
    """Make the temporal owner token from immutable captured content facts."""
    if isinstance(identity, Mapping):
        identity = [identity.get(name) for name in
                    ("owner_kind", "object_id", "primitive_id", "material")]
    if (not isinstance(identity, (list, tuple)) or len(identity) != 4
            or any(isinstance(v, bool) or not isinstance(v, int) for v in identity)
            or not isinstance(world, (list, tuple)) or len(world) != 4
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       and math.isfinite(float(v)) for v in world)
            or world[3] <= 0
            or not isinstance(revisions, (list, tuple)) or len(revisions) != 4
            or any(isinstance(v, bool) or not isinstance(v, int) for v in revisions)):
        raise ActualFieldError(f"{label} content facts are incomplete")
    if source_key is not None and revisions[3] != source_key[-2]:
        raise ActualFieldError(f"{label} content generation disagrees with source")
    # Capture generation, probe role and texel address establish the same-frame
    # source join. Temporal identity is the exact recorded content, independent
    # of a recapture assigning those same facts to another physical address.
    return ("content", tuple(identity), tuple(float(v) for v in world[:3]),
            tuple(int(v) for v in revisions[:3]))


def _presented_source_key(presented, label):
    source = presented.get("source") if isinstance(presented, Mapping) else None
    if (not isinstance(source, (list, tuple)) or len(source) != 4
            or any(isinstance(v, bool) or not isinstance(v, int) for v in source)
            or source[0] < 0 or source[1] < 0 or source[2] <= 0 or source[3] < 0):
        raise ActualFieldError(f"{label} presented source is incomplete")
    state = presented.get("state")
    if not isinstance(state, (list, tuple)) or len(state) < 2 or list(state[:2]) != [1, 1]:
        raise ActualFieldError(f"{label} presented source state is invalid")
    return ("front", *source)


def _prepack_source_key(row):
    provenance = row.get("probe_prepack_provenance", [])
    if (not isinstance(provenance, (list, tuple)) or len(provenance) < 3
            or any(isinstance(v, bool) or not isinstance(v, int) for v in provenance[:3])
            or provenance[0] < 0 or provenance[1] < 0 or provenance[2] < 0):
        return None
    return ("front", provenance[0] & 3, provenance[1], provenance[0] >> 2, provenance[2])


def _fact_from_presented(presented, source_key):
    world = presented.get("world")
    revisions = presented.get("revisions")
    identity = presented.get("identity")
    return _content_token(identity, world, revisions, "presented source", source_key)


def _fact_from_prepack(row, source_key):
    return _content_token(row.get("probe_prepack_identity"),
                          row.get("probe_prepack_world_position"),
                          row.get("probe_prepack_revisions"), "prepack source", source_key)


def _build_presented_source_lookup(rows):
    facts, ambiguous = {}, set()
    invalid = 0
    for row in rows:
        presented = row.get("presented_surface", {})
        try:
            key = _presented_source_key(presented, "presented")
            fact = _fact_from_presented(presented, key)
        except ActualFieldError:
            invalid += 1
            continue
        previous = facts.get(key)
        if previous is not None and previous != fact:
            ambiguous.add(key)
        else:
            facts[key] = fact
    for key in ambiguous:
        facts.pop(key, None)
    return facts, ambiguous, invalid


def _sky_content_token(content, source, key, label):
    """Join typed sky to its retained producer, independent of capture epoch."""
    layer_record = content.get("sky_context_layer")
    if not isinstance(layer_record, Mapping):
        return None
    if (layer_record.get("bank") != source["bank"]
            or layer_record.get("physical_layer") != key[-1]
            or layer_record.get("selected_sky_generation_token") != key[-2]):
        raise ActualFieldError(f"{label} retained sky source mismatch")
    params = layer_record.get("selected_product_texel_shade_params")
    frozen = layer_record.get("frozen_product_inputs")
    if not isinstance(params, Mapping) or not isinstance(frozen, Mapping):
        return None
    try:
        raw = bytes.fromhex(params["params_hex"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ActualFieldError(f"{label} retained sky params invalid") from exc
    if len(raw) != 400:
        raise ActualFieldError(f"{label} retained sky params size invalid")
    extent, layers, generation = struct.unpack_from("<3I", raw, 288)
    if (extent <= 0 or layers != 18 or generation <= 0
            or (generation | 0x80000000) != key[-2]):
        raise ActualFieldError(f"{label} retained sky generation invalid")
    address = source["reference"] & 0x0fffffff
    layer, local = divmod(address - 1, extent * extent)
    if layer != key[-1]:
        raise ActualFieldError(f"{label} retained sky address invalid")
    # The packed production UBO owns the actual ray origin and paint controls.
    # Other physical layers and their generation numbers do not identify this
    # source. Keep exact face/texel coordinates; never fit rays across frames.
    origin = struct.unpack_from("<4f", raw, layer * 16)
    if not all(math.isfinite(v) for v in origin):
        raise ActualFieldError(f"{label} retained sky origin invalid")
    required = ("product_frame_time_seconds", "sun_direction", "moon_direction",
                "cloud_time_seconds", "night_factor", "cloud_opacity")
    if any(name not in frozen for name in required):
        return None
    # These fields establish provenance and are validated by the source reader;
    # they are not inputs to Product's cubemap sky paint. Retain every other
    # recorded input, including advancing light, weather and source time.
    provenance = {"render_frame_index", "source_render_frame_index", "frame_index",
                  "sequence_frame", "camera_ray_origin", "scene_camera_origin",
                  "camera_forward", "camera_right", "camera_up", "camera_fov",
                  "camera_near", "camera_far", "world_to_clip_matrix"}
    inputs = {name: value for name, value in frozen.items() if name not in provenance}
    try:
        exact_inputs = json.dumps(inputs, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ActualFieldError(f"{label} retained sky inputs invalid") from exc
    # Mode, freshness, and layer-generation arrays control admission, not sky
    # colour. The retained producer is validated above before removing those
    # counters from temporal identity. Keep the actual near/far/range values.
    return ("sky_content", extent, layer % 6, local, origin[:3], raw[304:316], exact_inputs)


def _resolve_source_fact(field, source, label, presented_facts, ambiguous,
                         row, *, allow_prepack=False):
    key = _native_source_key(field, source, label)
    sky = source.get("capture_generation_kind") == "generated_sky"
    content_reader = getattr(field, "source_content", None)
    if callable(content_reader):
        # The complete observer snapshots every elected reference, including
        # paper and underlay sources absent from quarter-centre sparse rows.
        # Bank is the native display bank; effective capture fallback is a
        # separate recorded fact and cannot substitute for a missing bank.
        bank = 1 if source["bank"] == "underlay" else 0
        content = content_reader(source["reference"], bank)
        if not isinstance(content, Mapping) or content.get("status") != "ok":
            return None
        witness = content.get("key")
        if (not isinstance(witness, (list, tuple)) or len(witness) != 4
                or list(witness[:3]) != [source["reference"] & 0x0fffffff, bank, key[-2]]
                or not isinstance(witness[3], int) or not (witness[3] & 1)
                or bool(witness[3] & 4) != sky):
            raise ActualFieldError(f"{label} native content key mismatch")
        if sky:
            return _sky_content_token(content, source, key, label)
        fact = _content_token(content.get("identity"), content.get("world"),
                              content.get("revisions"), label, key)
        if key in ambiguous or (key in presented_facts and presented_facts[key] != fact):
            raise ActualFieldError(f"{label} native and sparse content contradict")
        captured_inputs = []
        for name in ("normal_depth", "anchor", "colour", "surface_meta"):
            values = content.get(name)
            if not isinstance(values, (list, tuple)) or len(values) != 4:
                return None
            if name == "surface_meta":
                if any(type(v) is not int or not 0 <= v <= 0xffffffff for v in values):
                    raise ActualFieldError(f"{label} captured material metadata invalid")
            elif not all(type(v) in (int, float) and math.isfinite(float(v)) for v in values):
                raise ActualFieldError(f"{label} captured {name} invalid")
            # Depth is relative to the capture origin, while the normal and
            # remaining facts are source paint inputs. Payload RGB is an output
            # and must never enter the key of a colour-instability comparison.
            captured_inputs.append(tuple(values[:3] if name == "normal_depth" else values))
        return ("captured_inputs", fact, tuple(captured_inputs))
    if sky:
        return None
    if key in ambiguous:
        return None
    fact = presented_facts.get(key)
    if fact is not None:
        return fact
    # Prepack identity is only a witness for the exact native reference and
    # generation.  It never identifies an unmatched channel by visibility.
    if allow_prepack and row.get("probe_prepack_valid") is True:
        if _prepack_source_key(row) == key:
            try:
                return _fact_from_prepack(row, key)
            except ActualFieldError:
                return None
    return None


def _actual_field_owner_key(field, x, y, presented_facts, ambiguous, row):
    """Return independent exact v2 FG/BG owners, or unresolved channels."""
    provenance_reader = getattr(field, "pixel_provenance", None)
    if not callable(provenance_reader):
        return None
    provenance = provenance_reader(x, y)
    front = provenance.get("front") if isinstance(provenance, Mapping) else None
    if not isinstance(front, Mapping):
        raise ActualFieldError("v2 final-paint record has no front source tuple")
    foreground = _resolve_source_fact(field, front.get("foreground"), "foreground",
                                       presented_facts, ambiguous, row)
    underlay_used = bool(provenance.get("underlay_used"))
    background = None if underlay_used else _resolve_source_fact(
        field, front.get("background"), "background", presented_facts,
        ambiguous, row, allow_prepack=True)
    underlay = None
    if underlay_used:
        under = provenance.get("underlay")
        if not isinstance(under, Mapping):
            raise ActualFieldError("v2 final-paint record has no underlay tuple")
        under_fg = _resolve_source_fact(field, under.get("foreground"),
                                         "underlay foreground", presented_facts,
                                         ambiguous, row)
        under_bg = _resolve_source_fact(field, under.get("background"),
                                        "underlay background", presented_facts,
                                        ambiguous, row, allow_prepack=True)
        underlay = (under_fg, under_bg)
        under_intensity = provenance.get("underlay_intensity")
        if (not isinstance(under_intensity, (int, float)) or isinstance(under_intensity, bool)
                or not math.isfinite(float(under_intensity)) or not 0 <= under_intensity <= 1):
            background = None
        elif under_fg is not None and under_bg is not None:
            # Production replaces paper with this mix. The earlier front
            # paper is no longer a contributor. A changed intact underlay
            # mask is a changed blend, not instability of unchanged paint.
            background = ("underlay_mix", under_fg, under_bg, float(under_intensity))
        else:
            background = None
    return foreground, background, underlay


def samples(rows, field):
    """Join colours to exact v2 elected owners at the gather pixel.

    Prepack identity remains a geometry witness, but it is not the owner of an
    FF colour unless its exact source reference and generation match the native
    channel. Each channel carries its own source token so a BG owner switch
    does not discard an otherwise exact FG comparison.
    """
    meta = field.metadata
    cols, rows_count = meta["semantic_ansi_size"]
    logical_w, logical_h = meta["logical_target_size"]
    bank = {}
    presented_facts, ambiguous_facts, invalid_presented = _build_presented_source_lookup(rows)
    invalid = clipped = missing_owner = missing_fg = missing_bg = 0
    for row in rows:
        world = row["probe_prepack_world_position"]
        if (row["probe_prepack_valid"] is not True or len(world) != 4
                or world[3] <= 0 or not all(math.isfinite(v) for v in world)):
            invalid += 1
            continue
        cell, slot = row["screen_cell"], row["subcell_slot"]
        if slot not in (0, 1, 2, 3):
            raise ActualFieldError("invalid prepack subcell slot")
        x = int((cell["x"] * 2 + slot % 2 + 0.5) * logical_w / (cols * 2))
        y = int((cell["y"] * 2 + slot // 2 + 0.5) * logical_h / (rows_count * 2))
        if not (0 <= x < field.width and 0 <= y < field.height):
            clipped += 1
            continue
        try:
            owner = _actual_field_owner_key(field, x, y, presented_facts,
                                            ambiguous_facts, row)
        except ActualFieldError:
            owner = None
        if owner is None:
            missing_owner += 1
            continue
        foreground, background, _underlay = owner
        missing_fg += foreground is None
        missing_bg += background is None
        if foreground is None and background is None:
            missing_owner += 1
            continue
        # This is the stable quarter-world anchor. The actual channel owners,
        # including their identity/world/revisions facts, stay in the value.
        key = (tuple(world[:3]), tuple(row["probe_prepack_revisions"][:3]))
        bank.setdefault(key, []).append((
            field.fields["fg_rgb"][y, x].copy(),
            field.fields["bg_rgb"][y, x].copy(),
            float(field.fields["effective_coverage"][y, x]),
            foreground,
            background,
        ))
    return bank, {"rows": len(rows), "invalid_source_rows": invalid,
                  "outside_native_image_rows": clipped,
                  "invalid_presented_source_rows": invalid_presented,
                  "ambiguous_presented_source_keys": len(ambiguous_facts),
                  "missing_actual_owner_rows": missing_owner,
                  "missing_foreground_owner_rows": missing_fg,
                  "missing_background_owner_rows": missing_bg,
                  "distinct_sources": len(bank)}


def compare_samples(before, after):
    deltas = {"fg": [], "bg": []}
    visible = {"fg": [], "bg": []}
    ambiguous = 0
    owner_mismatch = {"fg": 0, "bg": 0}
    unresolved = {"fg": 0, "bg": 0}
    shared = before.keys() & after.keys()
    for key in shared:
        # Ambiguity depends on source multiplicity, never colour: do not give
        # continuous modes a different denominator by merging equal colours.
        if len(before[key]) != 1 or len(after[key]) != 1:
            ambiguous += 1
            continue
        a, b = before[key][0], after[key][0]
        for i, channel in enumerate(("fg", "bg")):
            owner_index = 3 + i
            owner_a = a[owner_index] if len(a) > owner_index else True
            owner_b = b[owner_index] if len(b) > owner_index else True
            if owner_a is None or owner_b is None:
                unresolved[channel] += 1
                continue
            if owner_a != owner_b:
                owner_mismatch[channel] += 1
                continue
            delta = float(np.max(np.abs(a[i] - b[i])))
            deltas[channel].append(delta)
            seen = min(a[2], b[2]) > 0.01 if i == 0 else max(a[2], b[2]) < 0.99
            if seen:
                visible[channel].append(delta)

    def stats(values):
        changed = sum(v > THRESHOLD for v in values)
        return {"comparable_samples": len(values), "changed_samples": changed,
                "changed_pct": changed * 100.0 / len(values) if values else None,
                "max_channel_delta": max(values) if values else None,
                "mean_channel_delta": sum(values) / len(values) if values else None}

    return {"shared_sources": len(shared), "ambiguous_sources_excluded": ambiguous,
            "before_unmatched_sources": len(before.keys() - after.keys()),
            "after_unmatched_sources": len(after.keys() - before.keys()),
            "owner_mismatch": owner_mismatch,
            "unresolved_channel_samples": unresolved,
            "channels": {c: stats(v) for c, v in deltas.items()},
            "visible_channels": {c: stats(v) for c, v in visible.items()}}



def quarter_pixel(row, metadata):
    cell, slot = row["screen_cell"], row["subcell_slot"]
    grid = np.asarray(metadata["semantic_ansi_size"], dtype=float) * 2
    logical = np.asarray(metadata["logical_target_size"], dtype=float)
    subcell = np.asarray([cell["x"] * 2 + slot % 2,
                          cell["y"] * 2 + slot // 2])
    return np.floor((subcell + 0.5) * logical / grid) + 0.5


def quad_residual_px(point, corners, extent):
    """Distance from the actual fragment centre to its projected splat.

    A retained sample covers a finite quad; comparing its centre directly to
    the sample pixel would mislabel its legal footprint as jitter. The oracle
    checks the two actual triangles, without fitting or moving either one.
    """
    corners = np.asarray(corners, dtype=float)
    if corners.shape != (4, 4) or not np.isfinite(corners).all() or np.any(corners[:, 3] <= 0):
        return None
    q = (corners[:, :2] / corners[:, 3, None] * 0.5 + 0.5) * extent
    def edge(a, b):
        return (point[0] - a[0]) * (b[1] - a[1]) - (point[1] - a[1]) * (b[0] - a[0])
    for ids in ((0, 1, 3), (0, 3, 2)):
        vals = [edge(q[ids[i]], q[ids[(i + 1) % 3]]) for i in range(3)]
        area = np.linalg.det(np.stack((q[ids[1]]-q[ids[0]], q[ids[2]]-q[ids[0]])))
        if abs(area) > 1e-10 and (min(vals) >= 0 or max(vals) <= 0):
            return 0.0
    distances = []
    for a, b in ((0, 1), (1, 3), (3, 2), (2, 0)):
        delta = q[b] - q[a]
        denom = float(np.dot(delta, delta))
        t = np.clip(np.dot(point - q[a], delta) / denom, 0, 1) if denom else 0
        distances.append(float(np.linalg.norm(point - (q[a] + t * delta))))
    return min(distances)


def actual_quad_residual_px(point, corners, extent):
    """Check the executed splat quad without recomputing its corners."""
    if not isinstance(corners, (list, tuple)) or len(corners) != 4:
        return None
    # The vertex sidecar preserves production order [p00,p10,p11,p01];
    # quad_residual_px consumes [p00,p10,p01,p11].
    return quad_residual_px(point, [corners[0], corners[1], corners[3], corners[2]], extent)


def matches_eye_background(row, point, extent):
    """Identify the fullscreen backstop by its independently projected address.

    The background pass writes EYE capture data at depth one, without invoking
    the world-quad fragment shader. Do not ask that path for a quad witness.
    Depth one or EYE identity alone is insufficient to admit an exception.
    """
    if (row.get("probe_source_capture_join_flags") != 0x7f
            or row.get("probe_prepack_hardware_depth") != 1.0
            or row.get("probe_raster_candidate_source_contender_count") != 0):
        return False
    provenance = row.get("probe_prepack_provenance", [])
    if len(provenance) != 4 or int(provenance[0]) & 3:
        return False
    trace = row.get("frame_trace", {})
    state = trace.get("perspective_probe_state", {})
    size = state.get("probe_size", 0)
    if not isinstance(size, int) or size <= 0:
        return False
    try:
        matrix = np.asarray(trace["world_to_clip_matrix"]).reshape(4, 4, order="F")
        inverse = np.linalg.inv(matrix)
        ndc = np.asarray(point) / np.asarray(extent) * 2 - 1
        near = inverse @ np.r_[ndc, 1, 1]
        far = inverse @ np.r_[ndc, 0, 1]
        if near[3] == 0 or far[3] == 0:
            return False
        # Reversed-Z camera; unproject two points on the actual pixel's ray.
        direction = far[:3] / far[3] - near[:3] / near[3]
        if not np.isfinite(direction).all() or not np.any(direction):
            return False
        x, y, z = direction
        ax, ay, az = abs(direction)
        if ax >= ay and ax >= az:
            face, uv = (0 if x >= 0 else 1), np.array([-z if x >= 0 else z, -y]) / ax
        elif ay >= az:
            face, uv = (2 if y >= 0 else 3), np.array([x, z if y >= 0 else -z]) / ay
        else:
            face, uv = (4 if z >= 0 else 5), np.array([x if z >= 0 else -x, -y]) / az
        address = np.clip(np.floor((uv * .5 + .5) * size), 0, size - 1).astype(int)
        expected = int(face * size * size + address[1] * size + address[0])
        return expected == int(provenance[1])
    except (KeyError, ValueError, TypeError, np.linalg.LinAlgError):
        return False


def mask_error(observed, expected):
    if observed.shape != expected.shape:
        raise ActualFieldError("mask shape mismatch")
    actual = observed > 0.5
    return {"different_pixels": int(np.count_nonzero(actual != expected)),
            "observed_ink_pixels": int(np.count_nonzero(actual)),
            "expected_ink_pixels": int(np.count_nonzero(expected))}


def load_observed_atlases(metadata):
    records = metadata.get("provenance", {}).get("atlas_sources")
    if not isinstance(records, list) or len(records) != 3:
        raise ActualFieldError("runtime-bound atlas snapshots required for mask verdict")
    pages = {}
    for record in records:
        path = Path(record["path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ActualFieldError("runtime atlas snapshot hash mismatch")
        page_id = record["page"]
        if page_id in pages or page_id not in (0, 1, 2):
            raise ActualFieldError("duplicate or invalid atlas page")
        pages[page_id] = AtlasPage(page_id, record)
    return pages


def observe_presentation(rows, field, pages):
    counts = Counter()
    residuals, actual_residuals, failures = [], [], []
    actual_witness_requested = any(
        row.get("probe_actual_vertex_witness_enabled") is True for row in rows
    )
    actual_expected = actual_enabled = actual_missing = actual_invalid = actual_outside = 0
    cache, cells = {}, set()
    meta = field.metadata
    extent = np.asarray(meta["logical_target_size"])
    cw, ch = meta["cell_size"]
    terminal = pages[0].spec.get("native_terminal", False)
    layout = {}
    if terminal:
        # Independently reconstruct native advances from captured candidate
        # glyphs; compare the actual final GPU mask against the whole span.
        candidates = {(r["screen_cell"]["x"], r["screen_cell"]["y"]): r.get("presented_surface", {}).get("head", []) for r in rows}
        widths = pages[0].spec["native_widths"]
        for y in range(meta["semantic_ansi_size"][1]):
            x = 0
            while x < meta["semantic_ansi_size"][0]:
                head = candidates.get((x, y), [])
                if len(head) != 4:
                    raise ActualFieldError("native layout candidate missing")
                glyph = head[3] if head[0] & 1 else 63
                # FL-4691: corrupted/obsolete publication strides must fail as
                # evidence errors, never index arbitrary float bits as glyphs.
                if not isinstance(glyph, int) or not 0 <= glyph < len(widths):
                    raise ActualFieldError(
                        f"native candidate glyph {glyph!r} outside width table at cell {(x, y)}")
                span = widths[glyph]
                if span not in (1, 2):
                    raise ActualFieldError("native glyph advance unavailable")
                span = int(span)  # Godot JSON numbers round-trip as floats.
                for offset in range(span):
                    layout[(x + offset, y)] = (x, span, glyph)
                x += span
    for row in rows:
        cell = row["screen_cell"]
        xy = cell["x"], cell["y"]
        presented = row.get("presented_surface", {})
        if xy not in cells:
            cells.add(xy)
            head = presented.get("head", [])
            if len(head) != 4 or not head[0] & 1:
                counts["invalid_presented_cells"] += 1
            else:
                origin, span, glyph = layout[xy] if terminal else (xy[0], 1, head[3])
                page = next((p for p in pages.values()
                             if p.spec["glyph_min"] <= glyph <= p.spec["glyph_max"]), None)
                if page is None:
                    counts["unmapped_glyph_cells"] += 1
                else:
                    if glyph not in cache:
                        cache[glyph] = page.mask(glyph, ch, cw * span)
                    x, y = xy[0] * cw, xy[1] * ch
                    # Partial edge cells clip the whole-grid stamp; never resize it.
                    w, h = min(cw, field.width - x), min(ch, field.height - y)
                    if w > 0 and h > 0:
                        offset = (xy[0] - origin) * cw
                        err = mask_error(field.fields["native_intensity"][y:y+h, x:x+w],
                                         cache[glyph][:h, offset:offset+w])
                        counts["mask_cells"] += 1
                        counts["mask_mismatch_cells"] += err["different_pixels"] > 0
                        counts["mask_mismatch_pixels"] += err["different_pixels"]
                        if err["different_pixels"] and len(failures) < 12:
                            failures.append({"kind": "mask", "cell": xy, "glyph": glyph, **err})
        if not row["probe_prepack_valid"]:
            counts["no_world_source_quarters"] += 1
            continue
        counts["world_source_quarters"] += 1
        point = quarter_pixel(row, meta)
        if np.any(point >= [field.width, field.height]):
            counts["clipped_world_quarters"] += 1
            continue
        if row["probe_source_capture_join_flags"] != 0x7f:
            counts["source_join_failures"] += 1
            continue
        if row["probe_raster_contender_overflow"]:
            counts["contender_overflow_quarters"] += 1
            continue
        if row["probe_raster_candidate_source_contender_count"] <= 0:
            if matches_eye_background(row, point, extent):
                counts["verified_eye_background_quarters"] += 1
            else:
                counts["source_fragment_missing_quarters"] += 1
            continue
        depth = row["probe_prepack_hardware_depth"]
        lo = row["probe_raster_candidate_source_nearest_depth"]
        hi = row["probe_raster_candidate_source_farthest_depth"]
        tolerance = max(1e-7, abs(depth) * 1e-6)
        if not all(math.isfinite(v) for v in (depth, lo, hi)) or not lo-tolerance <= depth <= hi+tolerance:
            counts["source_fragment_depth_failures"] += 1
            continue
        # An armed capture must account for every admitted quarter through the
        # executed vertex sidecar. Missing or invalid sidecars fail this
        # verdict and never fall back to CPU-reconstructed corners.
        if actual_witness_requested:
            actual_expected += 1
            actual_enabled += int(row.get("probe_actual_vertex_witness_enabled") is True)
            if (row.get("probe_actual_vertex_witness_enabled") is not True
                    or row.get("probe_actual_vertex_witness_available") is not True):
                actual_missing += 1
            elif row.get("probe_actual_vertex_witness_valid") is not True:
                actual_invalid += 1
            else:
                actual_residual = actual_quad_residual_px(
                    point, row.get("probe_actual_vertex_clip_corners"), extent)
                if actual_residual is None:
                    actual_invalid += 1
                else:
                    actual_residuals.append(actual_residual)
                    actual_outside += actual_residual > 0.0
        if row.get("probe_actual_vertex_witness_valid") is True:
            actual_residual = actual_quad_residual_px(
                point, row.get("probe_actual_vertex_clip_corners"), extent)
            if actual_residual is None:
                counts["invalid_actual_vertex_quarters"] += 1
            else:
                counts["outside_actual_vertex_footprint_quarters"] += actual_residual > 0.0
        residual = quad_residual_px(point, row["probe_reconstructed_clip_corners"], extent)
        if not row["probe_reconstructed_clip_valid"] or residual is None:
            counts["invalid_projected_quarters"] += 1
            continue
        residuals.append(residual)
        # Report raw residuals; any positive distance remains a failure.
        # A hardware precision allowance needs separately calibrated evidence.
        counts["projected_quarters"] += 1
        counts["outside_projected_footprint_quarters"] += residual > 0.0
        if residual > 0.0 and len(failures) < 12:
            failures.append({"kind": "geometry", "cell": xy, "residual_px": residual})
    if actual_witness_requested:
        actual_vertex_geometry = {
            "verdict": "pass" if actual_expected > 0 and actual_enabled == actual_expected
                and actual_missing == 0 and actual_invalid == 0 and actual_outside == 0
                else "fail",
            "expected": actual_expected,
            "enabled": actual_enabled,
            "missing": actual_missing,
            "invalid": actual_invalid,
            "outside": actual_outside,
        }
    else:
        actual_vertex_geometry = {
            "verdict": "not_applicable",
            "expected": 0,
            "enabled": 0,
            "missing": 0,
            "invalid": 0,
            "outside": 0,
        }
    return {"counts": dict(counts), "examples": failures,
            "actual_vertex_geometry": actual_vertex_geometry,
            "max_footprint_residual_px": max(residuals) if residuals else None,
            "p99_footprint_residual_px": float(np.percentile(residuals, 99)) if residuals else None,
            "actual_vertex_witness_samples": len(actual_residuals),
            "max_actual_vertex_footprint_residual_px": max(actual_residuals) if actual_residuals else None,
            "p99_actual_vertex_footprint_residual_px": float(np.percentile(actual_residuals, 99)) if actual_residuals else None,
            "mask_pass": counts["mask_cells"] > 0 and counts["mask_mismatch_cells"] == 0
                and counts["unmapped_glyph_cells"] == 0 and counts["invalid_presented_cells"] == 0,
            "geometry_pass": counts["projected_quarters"] > 0 and not any(counts[k] for k in (
                "source_join_failures", "contender_overflow_quarters", "source_fragment_missing_quarters",
                "source_fragment_depth_failures", "invalid_projected_quarters", "outside_projected_footprint_quarters"))}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--presentation-verdict", action="store_true",
                        help="Require current raster witness and runtime atlases; grade masks and projected fragments")
    args = parser.parse_args()
    reader = ActualFieldReader(args.capture)
    previous = None
    readback_health = None
    frames, pairs = [], []
    observations, pages = [], None
    native, joined_native, capture_id = None, [], None
    if args.presentation_verdict:
        runtime = json.loads((args.capture.parent / "receipt.json").read_text())
        native = runtime.get("native_frame_capture", {})
        if native.get("status") != "readback_complete" or len(native.get("frames", [])) != 5:
            raise ActualFieldError("five post-flight native viewport frames required: " + str(native))
    for ordinal, (frame_id, trace, rows, raw_count) in enumerate(
            stream_frames(args.capture, 5, columns=COLUMNS)):
        if readback_health is None:
            log_path = trace.get("recorder_identity", {}).get("log_path", "")
            readback_health = runtime_readback_health(Path(log_path))
            if readback_health["status"] != "clean":
                raise ActualFieldError("runtime readback health is not clean: " + str(readback_health))
        field = reader.read(trace, frame_id=frame_id, sequence_index=ordinal)
        if raw_count != trace.get("oracle_expected_subcells_per_frame"):
            raise ActualFieldError("incomplete full-grid sample population")
        if args.presentation_verdict:
            if field.projection != "perspective" or field.metadata.get("presentation_compensation_active"):
                raise ActualFieldError("native presentation mapping for this projection is not yet verified")
            joined_native.append(validate_native_frame(native["frames"][ordinal], field))
            capture_id = field.capture_id
            if field.metadata.get("provenance", {}).get("observer_schema") != "fl4359.native_quarter_witness.v2":
                raise ActualFieldError("retired geometry observer cannot produce a presentation verdict")
            if field.metadata.get("tilt_shift_enabled") is not False or field.metadata.get("tilt_shift_intensity") != 0:
                raise ActualFieldError("tilt shift must be off in every measured frame")
            if pages is None:
                pages = load_observed_atlases(field.metadata)
            observations.append(observe_presentation(rows, field, pages))
        bank, population = samples(rows, field)
        frames.append({"frame_id": frame_id, "engine_frame": field.actual_frame_index,
                       "camera_world": trace.get("camera_world"),
                       "mode": field.policy.get("mode"),
                       "field_sha256": field.metadata["raw_sha256"], **population})
        if previous is not None:
            pairs.append({"before": frames[-2]["frame_id"], "after": frame_id,
                          **compare_samples(previous, bank)})
        previous = bank
        del field
        print(f"observed frame {frame_id}: {population}", flush=True)
    if len(frames) != 5 or any(b["engine_frame"] != a["engine_frame"] + 1
                               for a, b in zip(frames, frames[1:])):
        raise ActualFieldError("five contiguous native frames required")
    if len({f["mode"] for f in frames}) != 1:
        raise ActualFieldError("applied mode changed during capture")
    summary = {
        "schema": "fl4359.actual_field_stability.v2",
        "analyzer_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "capture": str(args.capture.resolve()),
        "source_join": "v2 front_sources/under_sources normalized to same-frame presented_surface.source [logical, face-local texel, generation, physical layer], then joined to presented identity/world/revisions facts; exact BG prepack witnesses are admitted only on the same tuple",
        "provenance_excluded_from_identity": "logical role, physical layer, face-local texel, capture generation (revisions.w), and publication digest; exact owner identity, world fact, and revisions.xyz remain temporal content evidence",
        "world_comparison": "exact_recorded_coordinates",
        "sample_lattice": "canonical full-grid quarter centres; original GLSL prepack_pixel formula",
        "threshold": THRESHOLD,
        "runtime_readback_health": readback_health,
        "claim_boundary": "Camera-associated FG/BG colour changes at matched world samples. Includes view-dependent lighting and cell-owner changes. Not a geometry jitter metric or acceptance verdict.",
        "frames": frames, "pairs": pairs,
        "all_pairs_have_comparable_samples": all(p["channels"]["bg"]["comparable_samples"] > 0 for p in pairs),
        "all_frames_have_complete_actual_owner_rows": all(
            f["missing_actual_owner_rows"] == 0 for f in frames),
        "all_frames_have_complete_foreground_owners": all(
            f["missing_foreground_owner_rows"] == 0 for f in frames),
        "all_frames_have_complete_background_owners": all(
            f["missing_background_owner_rows"] == 0 for f in frames),
        "acceptance_claimed": False,
    }
    digest = hashlib.sha256()
    with args.capture.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    summary["capture_sha256"] = digest.hexdigest()
    if args.presentation_verdict:
        provenance = native_presentation_provenance(runtime, reader.identity["provenance"]["atlas_sources"],
                                                     frames, reader.identity["policy"])
        summary["native_viewport_artifact"] = publish_native_gif(args.capture, joined_native, frames, capture_id,
                                                               provenance=provenance)
        summary["presentation_observations"] = observations
        summary["native_mask_verdict"] = "pass" if all(o["mask_pass"] for o in observations) else "fail"
        summary["projected_fragment_verdict"] = "pass" if all(o["geometry_pass"] for o in observations) else "fail"
        actual_verdicts = [o["actual_vertex_geometry"]["verdict"] for o in observations]
        summary["actual_vertex_geometry_verdict"] = (
            "pass" if actual_verdicts and all(v == "pass" for v in actual_verdicts)
            else "not_applicable" if actual_verdicts and all(v == "not_applicable" for v in actual_verdicts)
            else "fail"
        )
        summary["colour_verdict"] = {c: ("no_comparisons" if not all(p["channels"][c]["comparable_samples"] for p in pairs)
            else "changes_detected" if any(p["channels"][c]["changed_samples"] for p in pairs) else "stable_at_byte_tolerance") for c in ("fg", "bg")}
        summary["claim_boundary"] = "Measured native atlas-mask integrity, and same-frame world-quad footprint/depth or exact EYE-background address at admitted quarter pixels; camera-associated colour changes at matched world sources. Does not claim unobserved actors, other motion routes, or post-compositor viewport parity."
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2) + "\n")
    print(args.out, flush=True)


if __name__ == "__main__":
    main()
