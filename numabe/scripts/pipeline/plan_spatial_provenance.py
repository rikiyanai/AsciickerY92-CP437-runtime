#!/usr/bin/env python3
"""Spatial provenance pin for population plans (FL-4809 POPPLAN-01,
REVIEW v4 re-anchor directive).

One canonical metric world: a plan is compiled against one exact spatial
generation (canonical world IR + entities + terrain metadata + rules +
asset catalog). Lowerers verify the pin at entry and fail closed on any
drift. No best-effort lowering, no post-hoc snapping.

Hash rule: sha256 over JSON with volatile keys stripped (currently only
"generated_at", which the lane regenerates every build). Timestamps are
not spatial generation; everything else is. A plan pinned to file A
verifies against file B only when their spatial content is identical.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

try:
    from canonical_attachment import resolve as _attachment_resolve
except ImportError:  # sibling owner scripts/pipeline/canonical_attachment.py
    _attachment_resolve = None

SPATIAL_PROVENANCE_VERSION = "y92.plan_spatial_provenance.v1"


# Metadata regenerated every lane run that is not spatial generation.
VOLATILE_KEYS = frozenset({"generated_at"})


class PlanWorldMismatch(ValueError):
    """Raised when a plan's spatial pin does not match the inputs about
    to be lowered (or when the pin is absent). The message always starts
    with PLAN_WORLD_MISMATCH and names every differing field with both
    hashes."""


def _normalize(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in sorted(obj.items()):
            if k in VOLATILE_KEYS:
                continue
            if (k == "inputs" and isinstance(v, dict)
                    and all(isinstance(x, str) for x in v.values())):
                # FL-4809 BUILD-08: run-root path manifest, not spatial
                # generation; stripping lets an identical generation built
                # under another run root verify equal. A non-string inputs
                # entry is kept (fails safe toward mismatch).
                continue
            out[k] = _normalize(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_normalize(v) for v in obj]
    return obj


def spatial_sha256(path) -> str:
    """sha256 of a JSON document with volatile keys stripped; raw bytes
    for non-JSON files."""
    raw = Path(path).read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return hashlib.sha256(raw).hexdigest()
    canon = json.dumps(_normalize(doc), ensure_ascii=False,
                       sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stamp(*, canonical_world_ir_path, entities_path=None,
          terrain_metadata_path=None, rules_path,
          catalog_path=None, coordinate_frame: dict,
          world_units_per_metre: float, planner_version: str) -> dict:
    """Build the spatial_provenance header. Paths the planner does not
    consume pin as null with a reason instead of a hash."""
    header = {
        "schema": SPATIAL_PROVENANCE_VERSION,
        "canonical_world_ir_sha256": spatial_sha256(canonical_world_ir_path),
        "canonical_entities_sha256": (
            spatial_sha256(entities_path) if entities_path is not None
            else None),
        "terrain_metadata_sha256": (
            spatial_sha256(terrain_metadata_path)
            if terrain_metadata_path is not None else None),
        "rules_sha256": file_sha256(rules_path),
        "asset_catalog_sha256": (
            file_sha256(catalog_path) if catalog_path is not None
            else None),
        "coordinate_frame": dict(coordinate_frame),
        "world_units_per_metre": (
            float(world_units_per_metre)
            if world_units_per_metre is not None else None),
        "planner_version": str(planner_version),
        "unpinned_reasons": {},
    }
    reasons = header["unpinned_reasons"]
    if entities_path is None:
        reasons["canonical_entities_sha256"] = (
            "planner does not consume canonical entities")
    if terrain_metadata_path is None:
        reasons["terrain_metadata_sha256"] = (
            "planner was not given terrain metadata")
    if catalog_path is None:
        reasons["asset_catalog_sha256"] = (
            "no catalog assets consumed")
    return header


def verify(plan, canonical_world_ir_path, entities_path=None,
           terrain_metadata_path=None, rules_path=None,
           world_units_per_metre=None) -> dict:
    """Fail closed when the plan's pin disagrees with the inputs about
    to be lowered. Only pinned (non-null) fields are compared; catalog
    has no lower-time counterpart and stays provenance-only. Returns the
    pin on success."""
    doc = plan
    if isinstance(plan, (str, Path)):
        doc = json.loads(Path(plan).read_text(encoding="utf-8"))
    pin = (doc or {}).get("spatial_provenance")
    if not isinstance(pin, dict):
        raise PlanWorldMismatch("PLAN_WORLD_MISMATCH: spatial_provenance missing")
    diffs = []

    def _check(field, actual):
        pinned = pin.get(field)
        if pinned is None or actual is None:
            return
        if pinned != actual:
            diffs.append((field, pinned, actual))

    _check("canonical_world_ir_sha256",
           spatial_sha256(canonical_world_ir_path))
    if entities_path is not None:
        _check("canonical_entities_sha256",
               spatial_sha256(entities_path))
    if terrain_metadata_path is not None:
        _check("terrain_metadata_sha256",
               spatial_sha256(terrain_metadata_path))
    if rules_path is not None:
        _check("rules_sha256", file_sha256(rules_path))
    if world_units_per_metre is not None:
        pinned = pin.get("world_units_per_metre")
        if pinned is not None and float(pinned) != float(world_units_per_metre):
            diffs.append(("world_units_per_metre", pinned,
                          float(world_units_per_metre)))
    if diffs:
        lines = ["PLAN_WORLD_MISMATCH: plan spatial generation differs:"]
        for field, pinned, actual in diffs:
            lines.append(f"  {field}: pinned={pinned} actual={actual}")
        raise PlanWorldMismatch("\n".join(lines))
    return pin


_span_flip_cache: dict = {"works": None}


def span_flip_works(index) -> bool:
    """Probe whether canonical_attachment.resolve() honors carrier_span
    side (flips the offset for L). The span branch historically ignores
    the side field, mirroring every L-side span across its carrier.
    Cached per process: the helper code, not the index, decides."""
    if _span_flip_cache["works"] is not None:
        return _span_flip_cache["works"]
    if _attachment_resolve is None:
        raise ImportError(
            "span flip probe needs "
            "scripts/pipeline/canonical_attachment.py")
    for _fid, _poly in index.carriers:
        if len(_poly) < 2:
            continue
        _a, _b = _poly[0], _poly[1]
        _seg = math.hypot(_b[0] - _a[0], _b[1] - _a[1])
        if _seg <= 1e-9:
            continue
        _bearing = math.degrees(math.atan2(_b[1] - _a[1], _b[0] - _a[0]))
        _rec = {"anchor_kind": "carrier_span",
                "canonical_carrier_id": _fid,
                "start_chainage_m": 0.0, "end_chainage_m": _seg,
                "lateral_offset_m": 1.0, "side": "L"}
        _pose = _attachment_resolve(_rec, index)
        _nx = math.cos(math.radians(_bearing + 90.0))
        _ny = math.sin(math.radians(_bearing + 90.0))
        _flip = math.hypot(float(_pose["E"]) - (_a[0] - _nx),
                           float(_pose["N"]) - (_a[1] - _ny))
        _noflip = math.hypot(float(_pose["E"]) - (_a[0] + _nx),
                             float(_pose["N"]) - (_a[1] + _ny))
        _span_flip_cache["works"] = _flip < _noflip
        return _span_flip_cache["works"]
    _span_flip_cache["works"] = True
    return True


def resolve_span(record: dict, index: dict) -> dict:
    """resolve() a carrier_span record with side-flip compensation while
    the helper ignores side. Auto-disables the day the helper flips:
    positions stay correct under both versions. All other kinds pass
    straight through to resolve()."""
    if _attachment_resolve is None:
        raise ImportError(
            "span resolve needs "
            "scripts/pipeline/canonical_attachment.py")
    pose = _attachment_resolve(record, index)
    if record.get("side") == "L" and not span_flip_works(index):
        yaw = float(pose.get("yaw_deg", 0.0))
        off = float(record.get("lateral_offset_m", 0.0))
        nx = math.cos(math.radians(yaw + 90.0))
        ny = math.sin(math.radians(yaw + 90.0))
        pose = {"E": round(float(pose["E"]) - 2.0 * nx * off, 3),
                "N": round(float(pose["N"]) - 2.0 * ny * off, 3),
                "yaw_deg": pose.get("yaw_deg", 0.0)}
    return pose
