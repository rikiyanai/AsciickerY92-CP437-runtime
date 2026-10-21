#!/usr/bin/env python3
"""Machine feature-presence regression gate for Y9 render captures.

WHY THIS EXISTS
---------------
The repository already produces multi-gigabyte per-cell capture dumps and many
proof scripts, yet whole authored visual features (shoreline break glyphs,
cloud-shadow drift, actor half-sink, snow sparkle) went to ZERO on screen and no
machine check reported it. Every existing analyzer answers a narrow question
about one investigation. Nothing answers the blunt one:

    "For each authored feature, is its signature present in this capture at all,
     and was it supposed to be?"

This module is that intake-only detector. It turns a capture artifact into a
narrow per-signature presence verdict so a zero-signature feature RED-flags
automatically, and an expected-absent feature that RE-APPEARS also RED-flags.
It cannot satisfy the complete-feature coverage obligation owned by
``godot_feature_regression_gate.py``: source signatures, static proxies, and
capture-only evidence do not replace an executable behavior check.

OWNERSHIP
---------
This file owns feature *presence* verdicts only. It does not own correctness,
placement, or beauty of a feature, and it never claims final-framebuffer paint
proof beyond what the consumed capture surface itself claims.

INPUT SURFACES (see CAPABILITY_TABLE for the honest per-feature matrix)
----------------------------------------------------------------------
1. ``metadata.json``  - final_render_cell_dump session metadata. Owns the
   expected-when gates (``feature_state`` / ``weather_kind_name`` / actor rows).
2. ``cells.jsonl``    - one row per ANSI cell. In the CURRENT dump contract the
   ``cell_owner_*`` product fields are ALL NULL
   (``fact_readback_state == "cell_owner_product_readback_retired"``); the only
   authoritative per-cell payload is ``final_cell_paint_buffer`` which carries
   ``glyph_id``, ``owner_kind``, ``material_id``, fg/bg/center rgb, ink coverage
   and ``world_anchor_xz``. Glyph-id signatures therefore ride on
   ``final_cell_paint_buffer.glyph_id``.
   There is NO writer-tag / PRODUCT_CONTRIBUTED_* bit in the row schema, so
   writer-tag detection is impossible from this format (see CAPABILITY_TABLE).
3. ``*.png`` frames   - PixelCameraContext viewport readbacks, optionally with a
   ``<frame>.png.pixel_camera.json`` sidecar carrying ``frame_index`` and
   ``frame_time_seconds``. PNGs carry NO glyph identity; only colour, luma and
   temporal signatures can be read from them.

GLYPH CONSTANT PROVENANCE
-------------------------
Every glyph id below is transcribed from the CURRENT shader source, never
invented. See GLYPH_PROVENANCE for the exact file:line of each table.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

REPORT_SCHEMA = "feature_presence_report/v1"
# This analyzer intentionally covers only its small signature registry.  Keep
# the scope machine-readable so a caller cannot mistake intake evidence for
# the complete registry's executable behavior obligation.
ANALYZER_SCOPE = "intake-only"
COMPLETE_COVERAGE_OWNER = "scripts/godot_feature_regression_gate.py"

# ---------------------------------------------------------------------------
# Verdict vocabulary. RED_VERDICTS is the base set --strict fails on; is_red
# additionally fails explicit expectations that cannot be observed.
# ---------------------------------------------------------------------------
V_PRESENT = "PRESENT"
V_PRESENT_MOVING = "PRESENT_MOVING"
V_PRESENT_STATIC = "PRESENT_STATIC"
# The signature's spatial structure is above the contrast floor, but its
# TEMPORAL DELTA energy is below the floor, so this capture cannot separate
# "the contribution is frozen" from "the contribution is too weak to register
# above the static terrain-relief structure that shares the same spatial band".
# Both readings are regressions when the gate expected the feature, so the
# verdict is RED, but it must not be reported as the stronger PRESENT_STATIC
# claim, which asserts an existing field that provably fails to advance.
V_PRESENT_WEAK = "PRESENT_WEAK"
# The authored glyph identity is present but its paint colour does not carry the
# authored hue family (for example flower glyphs painted in grass-green). The
# feature is machine-detectable by glyph yet invisible to a human viewer.
V_PRESENT_WRONG_COLOR = "PRESENT_WRONG_COLOR"
V_MISSING = "MISSING"
V_GATED_OFF = "GATED_OFF"
V_UNEXPECTED_PRESENT = "UNEXPECTED_PRESENT"
# The feature's expected-when condition is not derivable from the capture (for
# example a world-anchored site that may simply be out of frame). A zero count
# is therefore not evidence of regression.
V_GATE_UNKNOWN = "GATE_UNKNOWN"
V_INSUFFICIENT_INPUT = "INSUFFICIENT_INPUT"

RED_VERDICTS = frozenset(
    {
        V_MISSING,
        V_PRESENT_STATIC,
        V_PRESENT_WEAK,
        V_PRESENT_WRONG_COLOR,
        V_UNEXPECTED_PRESENT,
    }
)

ALL_VERDICTS: tuple[str, ...] = (
    V_PRESENT,
    V_PRESENT_MOVING,
    V_PRESENT_STATIC,
    V_PRESENT_WEAK,
    V_PRESENT_WRONG_COLOR,
    V_MISSING,
    V_GATED_OFF,
    V_UNEXPECTED_PRESENT,
    V_GATE_UNKNOWN,
    V_INSUFFICIENT_INPUT,
)

# ---------------------------------------------------------------------------
# GLYPH CONSTANTS - transcribed from godot_project/render/product/cell_owner_product.glsl
# ---------------------------------------------------------------------------

GLYPH_PROVENANCE = {
    "FL4216_GLYPH_ID": "godot_project/render/product/cell_owner_product.glsl:3390 "
    "(const int FL4216_GLYPH_ID[32], 8 directions N,NE,E,SE,S,SW,W,NW x 4 stages)",
    "WATERFALL_GLYPH_DIR": "godot_project/render/product/cell_owner_product.glsl:6405 "
    "(WATERFALL_GLYPH_DIR = 4, the S row; consumed at :6444)",
    "flowers": "godot_project/render/product/cell_owner_product.glsl:2579 (dense_flowers[8]), "
    ":2580 (mid_flowers[4]), :7962 (flower_top[4])",
    "water_activity": "godot_project/render/product/cell_owner_product.glsl:4860 "
    "(product_water_activity_glyph)",
    "rain_blades": "godot_project/render/product/cell_owner_product.glsl:6175 "
    "(cell_owner_rain_blade_glyph proxies | | || |)",
    "snow_fall": "godot_project/render/product/cell_owner_product.glsl:6328 (snow_glyphs[4])",
    "snow_sparkle": "godot_project/render/product/cell_owner_product.glsl:6322 (glyph = 0xB7u)",
    "sky_cloud_bodies": "godot_project/render/product/cell_owner_product.glsl:5347/:5353 "
    "(paint.glyph = 0xB1u / 0xB0u in the sky celestial/cloud body branch)",
    "waterfall_writer_bit": "godot_project/render/product/cell_owner_product.glsl:9996 "
    "(PRODUCT_CONTRIBUTED_WATERFALL_BIT = 1u << 25u) - NOT exported to cells.jsonl",
    "birds": "godot_project/scripts/visual/BirdFlock3D.gd "
    "(retained rigid mesh bird_flock_v1, kind \"bird_face\", FL-4907 N16); FL-4187 "
    "birds are world geometry, so there is no shader glyph constant",
}

# 8 directions (N,NE,E,SE,S,SW,W,NW) x 4 stages -> extended atlas glyph id.
# Row-major exactly as authored in the shader; index = dir * 4 + stage.
FL4216_GLYPH_ID: tuple[int, ...] = (
    664, 546, 661, 661,   # N
    668, 649, 662, 662,   # NE
    517, 648, 652, 657,   # E
    654, 556, 647, 659,   # SE
    665, 656, 660, 660,   # S
    669, 653, 651, 657,   # SW
    666, 663, 663, 650,   # W
    667, 655, 658, 658,   # NW
)
FL4216_BREAK_GLYPH_IDS = frozenset(FL4216_GLYPH_ID)

# The waterfall reuses the S row of the same table so its bands read as falling.
WATERFALL_GLYPH_DIR = 4
WATERFALL_GLYPH_IDS = frozenset(
    FL4216_GLYPH_ID[WATERFALL_GLYPH_DIR * 4 + stage] for stage in range(4)
)

# Authored flower glyph sets: dense_flowers[8] U mid_flowers[4] U flower_top[4].
FLOWER_GLYPH_IDS = frozenset({620, 622, 628, 630, 647, 629, 624, 625})

# product_water_activity_glyph: wake (~ / \), phase 1 -> 670, phase 2 -> '=', else '~'.
WATER_ACTIVITY_GLYPH_IDS = frozenset({670, 0x3D, 0x7E, 0x2F, 0x5C})
# Phase-varying subset used for the temporal check (wake glyphs excluded because
# a wake only exists while the player swims).
WATER_ACTIVITY_PHASE_GLYPH_IDS = frozenset({670, 0x3D, 0x7E})

RAIN_BLADE_GLYPH_IDS = frozenset({0x7C, 0xB3, 0xBA})
SNOW_FALL_GLYPH_IDS = frozenset({0x2A, 0x2B, 0x2E, 0x2C})
SNOW_SPARKLE_GLYPH_ID = 0xB7
SKY_CLOUD_BODY_GLYPH_IDS = frozenset({0xB0, 0xB1})

# ---------------------------------------------------------------------------
# Capture loading
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """Compact per-cell view.

    Only the fields any registered signature needs are retained so that a
    multi-gigabyte cells.jsonl streams in bounded memory.
    """

    x: int
    y: int
    glyph_id: int | None
    owner_kind: str | None
    material_id: int | None
    luma: float | None
    world_xz: tuple[float, float] | None
    line_no: int
    # final_cell_paint_buffer.fg_rgb, linear float triple in 0..1. MEASURED
    # 100% populated on the real 2026-07-29 dump (21060/21060 rows), so a hue
    # check on glyph paint is a real capability of the current dump contract.
    fg_rgb: tuple[float, float, float] | None = None
    # Non-null cell_owner_* / actor fact names seen on this row. Used by the
    # field-presence detectors (actor_submerge, birds) which have no glyph.
    facts: tuple[str, ...] = ()


@dataclass
class CellCapture:
    path: Path
    cells: list[Cell]
    by_xy: dict[tuple[int, int], Cell]
    owner_counts: dict[str, int]
    feature_flags: dict[str, Any]
    rows: int
    cols: int


@dataclass
class FrameCapture:
    path: Path
    frame_index: float | None
    frame_time_seconds: float | None


@dataclass
class Capture:
    """Everything one invocation was given, already normalised."""

    roots: list[Path] = field(default_factory=list)
    metadata: dict[str, Any] | None = None
    metadata_path: Path | None = None
    cell_captures: list[CellCapture] = field(default_factory=list)
    frames: list[FrameCapture] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def primary_cells(self) -> CellCapture | None:
        return self.cell_captures[0] if self.cell_captures else None


_FACT_FIELDS_OF_INTEREST = (
    "cell_owner_waterline_clip",
    "cell_owner_actor_waterline_clip_threshold",
    "cell_owner_actor_sink_foot_world",
    "actor_foot_sink_world",
    "actor_foot_sink_screen_delta_px",
    "cell_owner_cloud_shadow_density",
    "cell_owner_shadow_class",
    "cell_owner_shadow_density",
    "cell_owner_terrain_snow_fact",
    "dynamic_mover_owner_kind",
    "airframe_id",
)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    return None


def _rgb_triple(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        return None
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        return None


# --- hue families -----------------------------------------------------------
#
# The flower registry entry needs to answer "is this glyph painted in a colour a
# player would read as a flower, or is it painted as more grass?".  The authored
# flower palettes are pink / blue / white / yellow; ordinary grass and foliage
# paint sits in the yellow-green..cyan wedge.  Hue bounds are in degrees on the
# HSV circle computed from the fg_rgb triple.
#
# MEASURED on the real 2026-07-29 dump: terrain fg hues bucket almost entirely
# into 60-119 deg (13487 of 13533 terrain cells), which is exactly the
# grass-dominant wedge these bounds exclude.
GRASS_HUE_RANGE_DEG = (61.0, 175.0)
# Below this saturation a colour reads as neutral; a bright neutral is the
# authored WHITE flower family, a dark neutral is unpainted shadow.
NEUTRAL_SATURATION_MAX = 0.18
WHITE_FLOWER_VALUE_MIN = 0.45
# Chromatic hue wedges that read as flower paint. (name, hue_low, hue_high) deg.
FLOWER_HUE_FAMILIES: tuple[tuple[str, float, float], ...] = (
    ("yellow", 40.0, 61.0),
    ("blue", 185.0, 265.0),
    ("purple", 265.0, 300.0),
    ("pink", 300.0, 360.0),
    ("pink_wrap", 0.0, 25.0),
)
FLOWER_CHROMA_SATURATION_MIN = 0.20


def _hue_family(fg: tuple[float, float, float]) -> str:
    """Classify one fg colour into a coarse family name.

    Returns ``"grass"`` for the grass-dominant wedge, ``"dark"`` for anything
    too dark to read as paint, ``"white"`` for a bright neutral, or one of the
    FLOWER_HUE_FAMILIES names. ``"other"`` covers desaturated off-wedge paint.
    """

    import colorsys

    red, grn, blue = (min(1.0, max(0.0, c)) for c in fg)
    hue, sat, val = colorsys.rgb_to_hsv(red, grn, blue)
    hue_deg = hue * 360.0
    if val < 0.12:
        return "dark"
    if sat < NEUTRAL_SATURATION_MAX:
        return "white" if val >= WHITE_FLOWER_VALUE_MIN else "dark"
    if GRASS_HUE_RANGE_DEG[0] <= hue_deg < GRASS_HUE_RANGE_DEG[1]:
        return "grass"
    if sat >= FLOWER_CHROMA_SATURATION_MIN:
        for name, low, high in FLOWER_HUE_FAMILIES:
            if low <= hue_deg < high:
                return "pink" if name == "pink_wrap" else name
    return "other"


FLOWER_HUE_FAMILY_ALLOWLIST = frozenset({"yellow", "blue", "purple", "pink", "white"})


def _row_to_cell(row: dict[str, Any], line_no: int) -> Cell | None:
    screen = row.get("screen_cell") or {}
    x = _as_int(screen.get("x"))
    y = _as_int(screen.get("y"))
    if x is None or y is None:
        return None
    paint = row.get("final_cell_paint_buffer") or {}
    glyph_id = _as_int(paint.get("glyph_id"))
    if glyph_id is None:
        # Older dumps put the id at the row root; fall back before giving up.
        glyph_id = _as_int(row.get("glyph_id"))
    owner_kind = paint.get("owner_kind") or row.get("cell_owner_kind")
    if isinstance(owner_kind, str):
        owner_kind = owner_kind.strip() or None
    else:
        owner_kind = None
    anchor = paint.get("world_anchor_xz")
    world_xz: tuple[float, float] | None = None
    if isinstance(anchor, (list, tuple)) and len(anchor) >= 2:
        try:
            world_xz = (float(anchor[0]), float(anchor[1]))
        except (TypeError, ValueError):
            world_xz = None
    luma = row.get("luma")
    fg_rgb = _rgb_triple(paint.get("fg_rgb"))
    if fg_rgb is None:
        # Older dumps carry the paint colour at the row root instead.
        fg_rgb = _rgb_triple(row.get("fg_rgb"))
    facts = tuple(
        name
        for name in _FACT_FIELDS_OF_INTEREST
        if row.get(name) not in (None, False, "", [], {})
    )
    return Cell(
        x=x,
        y=y,
        glyph_id=glyph_id,
        owner_kind=owner_kind,
        material_id=_as_int(paint.get("material_id")),
        luma=float(luma) if isinstance(luma, (int, float)) else None,
        world_xz=world_xz,
        line_no=line_no,
        fg_rgb=fg_rgb,
        facts=facts,
    )


def load_cells(path: Path) -> CellCapture:
    cells: list[Cell] = []
    owner_counts: dict[str, int] = {}
    feature_flags: dict[str, Any] = {}
    max_x = max_y = -1
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if not feature_flags and isinstance(row.get("feature_flags"), dict):
                feature_flags = dict(row["feature_flags"])
            cell = _row_to_cell(row, line_no)
            if cell is None:
                continue
            cells.append(cell)
            key = cell.owner_kind or "none"
            owner_counts[key] = owner_counts.get(key, 0) + 1
            max_x = max(max_x, cell.x)
            max_y = max(max_y, cell.y)
    by_xy = {(c.x, c.y): c for c in cells}
    return CellCapture(
        path=path,
        cells=cells,
        by_xy=by_xy,
        owner_counts=owner_counts,
        feature_flags=feature_flags,
        rows=max_y + 1,
        cols=max_x + 1,
    )


def load_frame(path: Path) -> FrameCapture:
    sidecar = path.with_name(path.name + ".pixel_camera.json")
    frame_index: float | None = None
    frame_time: float | None = None
    if sidecar.is_file():
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
        if isinstance(meta, dict):
            value = meta.get("frame_index")
            if isinstance(value, (int, float)):
                frame_index = float(value)
            value = meta.get("frame_time_seconds")
            if isinstance(value, (int, float)):
                frame_time = float(value)
    return FrameCapture(path=path, frame_index=frame_index, frame_time_seconds=frame_time)


def discover_capture(root: Path, capture: Capture) -> None:
    """Populate `capture` from a capture directory laid out by the dump writers."""

    capture.roots.append(root)
    metadata_path = root / "metadata.json"
    if metadata_path.is_file() and capture.metadata is None:
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            capture.notes.append(f"metadata.json unreadable at {metadata_path}: {exc}")
        else:
            if isinstance(loaded, dict):
                capture.metadata = loaded
                capture.metadata_path = metadata_path
    cells_path = root / "cells.jsonl"
    if cells_path.is_file():
        capture.cell_captures.append(load_cells(cells_path))
    else:
        capture.notes.append(
            f"no cells.jsonl under {root}; glyph-identity features are "
            "INSUFFICIENT_INPUT for this capture"
        )
    pngs = sorted(p for p in root.glob("*.png") if p.is_file())
    frames = [load_frame(p) for p in pngs]
    # A capture directory also holds derived images (hud_crop.png, *_zoom.png).
    # Those are not viewport frames and must never enter a temporal comparison.
    # The PixelCameraContext sidecar is the only reliable "this is a real frame"
    # marker, so when ANY sidecar is present the unsidecar'd PNGs are dropped.
    sidecar_backed = [f for f in frames if f.frame_index is not None]
    if sidecar_backed:
        dropped = len(frames) - len(sidecar_backed)
        frames = sidecar_backed
        if dropped:
            capture.notes.append(
                f"{dropped} PNG(s) under {root} have no .pixel_camera.json sidecar "
                "and were excluded as derived crops, not viewport frames"
            )
    frames.sort(
        key=lambda f: (
            f.frame_index if f.frame_index is not None else math.inf,
            f.path.name,
        )
    )
    capture.frames.extend(frames)


# ---------------------------------------------------------------------------
# Session gates
# ---------------------------------------------------------------------------


@dataclass
class Gates:
    """Expected-when inputs computed for one capture."""

    feature_state: dict[str, Any]
    weather_kind_name: str | None
    actor_water_contact: bool
    source: str

    def flag(self, name: str, default: bool | None = None) -> bool | None:
        value = self.feature_state.get(name, default)
        if isinstance(value, bool):
            return value
        return default


def resolve_gates(capture: Capture) -> Gates:
    state: dict[str, Any] = {}
    source_parts: list[str] = []
    if capture.metadata:
        raw = capture.metadata.get("feature_state")
        if isinstance(raw, dict):
            state.update(raw)
            source_parts.append("metadata.json:feature_state")
    primary = capture.primary_cells
    if primary and primary.feature_flags:
        for key, value in primary.feature_flags.items():
            state.setdefault(key, value)
        source_parts.append("cells.jsonl:feature_flags")
    weather = state.get("weather_kind_name")
    if capture.metadata and not weather:
        weather = capture.metadata.get("weather_kind_name")
    actor_water = False
    if capture.metadata:
        rows = capture.metadata.get("actor_rows")
        if isinstance(rows, list):
            actor_water = any(
                isinstance(r, dict) and bool(r.get("actor_water_contact")) for r in rows
            )
    return Gates(
        feature_state=state,
        weather_kind_name=weather if isinstance(weather, str) else None,
        actor_water_contact=actor_water,
        source=" + ".join(source_parts) or "none",
    )


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


@dataclass
class Detection:
    """Raw signature measurement, before the expected-when gate is applied."""

    count: int
    evidence: list[str]
    detail: dict[str, Any] = field(default_factory=dict)
    insufficient: str | None = None
    # Optional refinement used by temporal features: overrides PRESENT.
    present_verdict: str = V_PRESENT


def _locator(capture: CellCapture, cell: Cell, extra: str = "") -> str:
    suffix = f" {extra}" if extra else ""
    return (
        f"{capture.path}:{cell.line_no} screen_cell=({cell.x},{cell.y}) "
        f"glyph_id={cell.glyph_id} owner={cell.owner_kind}{suffix}"
    )


def _neighbour_has_owner(
    capture: CellCapture, cell: Cell, owners: Sequence[str], radius: int
) -> bool:
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                continue
            other = capture.by_xy.get((cell.x + dx, cell.y + dy))
            if other is not None and other.owner_kind in owners:
                return True
    return False


def detect_glyph_set(
    capture: Capture,
    *,
    glyph_ids: Iterable[int],
    owner_kinds: Sequence[str] | None = None,
    require_neighbour_owner: Sequence[str] | None = None,
    neighbour_radius: int = 2,
    top_row_fraction: float | None = None,
    hue_families: Iterable[str] | None = None,
    min_hue_cells: int = 3,
    min_hue_fraction: float = 0.10,
    max_evidence: int = 3,
) -> Detection:
    """Count cells painting one of ``glyph_ids``, optionally with a hue check.

    When ``hue_families`` is given the detector additionally classifies each
    matching cell's ``final_cell_paint_buffer.fg_rgb`` (see ``_hue_family``).
    Glyph identity alone is not visibility: a flower glyph painted in the
    grass-dominant hue wedge is machine-detectable and invisible on screen, so
    that case returns PRESENT_WRONG_COLOR instead of PRESENT.

    The hue check is skipped, and recorded as skipped in ``detail``, when the
    consumed rows carry no fg colour at all (older dump contracts).
    """

    cells_capture = capture.primary_cells
    if cells_capture is None:
        return Detection(0, [], insufficient="no cells.jsonl in capture")
    wanted = frozenset(glyph_ids)
    owners = tuple(owner_kinds) if owner_kinds else None
    neighbours = tuple(require_neighbour_owner) if require_neighbour_owner else None
    families = frozenset(hue_families) if hue_families is not None else None
    row_limit = (
        int(cells_capture.rows * top_row_fraction)
        if top_row_fraction is not None and cells_capture.rows > 0
        else None
    )
    count = 0
    per_glyph: dict[int, int] = {}
    evidence: list[str] = []
    hue_counts: dict[str, int] = {}
    hue_ok = 0
    fg_rows = 0
    hue_evidence: list[str] = []
    for cell in cells_capture.cells:
        if cell.glyph_id is None or cell.glyph_id not in wanted:
            continue
        if owners is not None and cell.owner_kind not in owners:
            continue
        if row_limit is not None and cell.y >= row_limit:
            continue
        if neighbours is not None and not _neighbour_has_owner(
            cells_capture, cell, neighbours, neighbour_radius
        ):
            continue
        count += 1
        per_glyph[cell.glyph_id] = per_glyph.get(cell.glyph_id, 0) + 1
        if len(evidence) < max_evidence:
            evidence.append(_locator(cells_capture, cell))
        if families is None:
            continue
        if cell.fg_rgb is None:
            continue
        fg_rows += 1
        family = _hue_family(cell.fg_rgb)
        hue_counts[family] = hue_counts.get(family, 0) + 1
        if family in families:
            hue_ok += 1
            if len(hue_evidence) < max_evidence:
                hue_evidence.append(
                    _locator(cells_capture, cell, f"fg_hue_family={family}")
                )
    detail: dict[str, Any] = {
        "per_glyph_id": {str(k): v for k, v in sorted(per_glyph.items())},
        "owner_kind_filter": list(owners) if owners else None,
        "cells_scanned": len(cells_capture.cells),
    }
    if families is None:
        return Detection(count=count, evidence=evidence, detail=detail)
    detail.update(
        {
            "hue_families_required": sorted(families),
            "hue_family_counts": dict(sorted(hue_counts.items())),
            "hue_matching_cells": hue_ok,
            "min_hue_cells": min_hue_cells,
            "min_hue_fraction": min_hue_fraction,
            "cells_with_fg_rgb": fg_rows,
        }
    )
    if count == 0:
        return Detection(count=0, evidence=evidence, detail=detail)
    if fg_rows == 0:
        detail["hue_check"] = (
            "SKIPPED - no fg_rgb on any matching row in this dump contract; "
            "glyph presence only, colour visibility NOT measured"
        )
        return Detection(count=count, evidence=evidence, detail=detail)
    hue_floor = max(min_hue_cells, int(math.ceil(min_hue_fraction * fg_rows)))
    detail["hue_floor_applied"] = hue_floor
    if hue_ok >= hue_floor:
        detail["hue_check"] = "PASS - authored hue family present on the glyph cells"
        return Detection(
            count=count, evidence=evidence + hue_evidence[:max_evidence], detail=detail
        )
    detail["hue_check"] = (
        f"FAIL - {hue_ok} of {fg_rows} glyph cells carry an authored hue family "
        f"(floor {hue_floor}); the glyphs are painted in a non-authored colour and "
        "are therefore invisible to a viewer"
    )
    return Detection(
        count=count,
        evidence=evidence,
        detail=detail,
        present_verdict=V_PRESENT_WRONG_COLOR,
    )


def detect_vertical_run(
    capture: Capture,
    *,
    glyph_ids: Iterable[int],
    owner_kinds: Sequence[str] | None,
    min_run: int,
    max_evidence: int = 3,
) -> Detection:
    """A world-anchored falling ribbon reads as a vertical run in one column."""

    cells_capture = capture.primary_cells
    if cells_capture is None:
        return Detection(0, [], insufficient="no cells.jsonl in capture")
    wanted = frozenset(glyph_ids)
    owners = tuple(owner_kinds) if owner_kinds else None
    columns: dict[int, list[Cell]] = {}
    for cell in cells_capture.cells:
        if cell.glyph_id is None or cell.glyph_id not in wanted:
            continue
        if owners is not None and cell.owner_kind not in owners:
            continue
        columns.setdefault(cell.x, []).append(cell)
    runs = 0
    run_cells = 0
    evidence: list[str] = []

    def _close(run: list[Cell]) -> None:
        nonlocal runs, run_cells
        if len(run) < min_run:
            return
        runs += 1
        run_cells += len(run)
        if len(evidence) < max_evidence:
            evidence.append(
                _locator(cells_capture, run[0], f"vertical_run_len={len(run)}")
            )

    for x in sorted(columns):
        ordered = sorted(columns[x], key=lambda c: c.y)
        run: list[Cell] = []
        for cell in ordered:
            if run and cell.y == run[-1].y + 1:
                run.append(cell)
            else:
                _close(run)
                run = [cell]
        _close(run)
    return Detection(
        count=runs,
        evidence=evidence,
        detail={
            "min_run": min_run,
            "run_cells": run_cells,
            "candidate_cells": sum(len(v) for v in columns.values()),
        },
    )


def detect_fact_fields(
    capture: Capture,
    *,
    fields: Sequence[str],
    owner_kinds: Sequence[str] | None = None,
    max_evidence: int = 3,
) -> Detection:
    cells_capture = capture.primary_cells
    if cells_capture is None:
        return Detection(0, [], insufficient="no cells.jsonl in capture")
    wanted = frozenset(fields)
    owners = tuple(owner_kinds) if owner_kinds else None
    count = 0
    evidence: list[str] = []
    hits: dict[str, int] = {}
    for cell in cells_capture.cells:
        if owners is not None and cell.owner_kind not in owners:
            continue
        matched = [name for name in cell.facts if name in wanted]
        if not matched:
            continue
        count += 1
        for name in matched:
            hits[name] = hits.get(name, 0) + 1
        if len(evidence) < max_evidence:
            evidence.append(_locator(cells_capture, cell, f"facts={','.join(matched)}"))
    return Detection(
        count=count,
        evidence=evidence,
        detail={"fields_scanned": list(fields), "field_hits": hits},
    )


def detect_owner_kind(
    capture: Capture, *, owner_kinds: Sequence[str], max_evidence: int = 3
) -> Detection:
    cells_capture = capture.primary_cells
    if cells_capture is None:
        return Detection(0, [], insufficient="no cells.jsonl in capture")
    owners = frozenset(owner_kinds)
    count = 0
    evidence: list[str] = []
    for cell in cells_capture.cells:
        if cell.owner_kind in owners:
            count += 1
            if len(evidence) < max_evidence:
                evidence.append(_locator(cells_capture, cell))
    return Detection(count=count, evidence=evidence, detail={"owner_kinds": list(owners)})


# --- temporal ---------------------------------------------------------------


def _frame_luma_grid(path: Path, cols: int, rows: int):
    """Downsample a frame to a cols x rows luma grid (float32, 0..1)."""

    from PIL import Image  # imported lazily so cells-only runs need no Pillow
    import numpy as np

    with Image.open(path) as image:
        grey = image.convert("L").resize((cols, rows), Image.BOX)
        return np.asarray(grey, dtype="float32") / 255.0


def _box_low_pass(grid, cell_size: int):
    """Box-average `grid` over cell_size x cell_size blocks and re-expand.

    Structure FINER than ``cell_size`` grid cells is removed; structure coarser
    than it survives. Kept in float32 (PIL mode "F") rather than the uint8
    round-trip the first implementation used, because the cloud band lives
    within a few 1/255 steps of the contrast floor and 8-bit quantisation
    (0.0039) was a third of that floor.
    """

    from PIL import Image
    import numpy as np

    rows, cols = grid.shape
    step = max(1, int(cell_size))
    small_cols = max(2, cols // step)
    small_rows = max(2, rows // step)
    image = Image.fromarray(np.asarray(grid, dtype="float32"), mode="F")
    back = image.resize((small_cols, small_rows), Image.BOX).resize(
        (cols, rows), Image.BILINEAR
    )
    return np.asarray(back, dtype="float32")


def _detail_high_pass(grid, cell_size: int = 10):
    """Residual after removing structure coarser than ``cell_size``.

    This keeps FINE, glyph/cell-scale detail. It is the right filter for the
    god-ray anisotropy proxy, whose signature is thin vertical shafts, and it is
    the WRONG filter for the cloud-shadow field (see ``_cloud_band_pass``).
    """

    return grid - _box_low_pass(grid, cell_size)


# The cloud-shadow field is a LOW-frequency blotch field (roughly 15 world units
# across, i.e. tens of ANSI cells), not fine texture.  These two box sizes are in
# analysis-grid cells and bracket that band:
#   * finer than CLOUD_BAND_FINE_CELLS  -> per-cell terrain relief / material
#     dither, which is SCREEN-STATIC and therefore correlates at (0,0) no matter
#     how fast the clouds move;
#   * coarser than CLOUD_BAND_COARSE_CELLS -> the whole-scene lighting gradient
#     and the DC term, which carry no drift information and would swamp the
#     correlation denominator.
#
# MEASURED sweep on a synthetic 8-cell blob translating 3 cells under static
# per-cell texture (frame amplitude 0.35): the old high-pass reported (0,0);
# fine_cells 2/3/4 recovered (1,0)/(2,0)/(2,0); fine_cells 5 and 6 recovered the
# exact (3,0). 5 is used because 6 starts eroding the smaller blotch scales.
CLOUD_BAND_FINE_CELLS = 5
CLOUD_BAND_COARSE_CELLS = 20


def _cloud_band_pass(
    grid,
    *,
    fine_cells: int = CLOUD_BAND_FINE_CELLS,
    coarse_cells: int = CLOUD_BAND_COARSE_CELLS,
):
    """Isolate the cloud-scale luma band as difference-of-box-low-pass.

    POLARITY: the first implementation returned ``grid - low_pass(grid, 10)``,
    the HIGH-pass residual.  That kept glyph-scale texture and discarded the
    entire cloud band, so ``detect_cloud_shadow_drift`` cross-correlated
    screen-locked terrain texture and reported a (0,0) peak even at a +/-30 x
    +/-40 search radius.  The detector must consume the LOW-pass field with the
    glyph texture averaged away, not the residual that is only glyph texture.
    """

    return _box_low_pass(grid, fine_cells) - _box_low_pass(grid, coarse_cells)


def _best_shift(a, b, max_shift: int):
    """Normalised cross-correlation peak over integer (dx, dy) shifts."""

    import numpy as np

    rows, cols = a.shape
    pad = max_shift
    if rows <= 2 * pad + 2 or cols <= 2 * pad + 2:
        return (0, 0), 0.0
    core = a[pad : rows - pad, pad : cols - pad]
    core = core - core.mean()
    core_norm = float(np.sqrt((core * core).sum()))
    best = ((0, 0), -2.0)
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            window = b[pad + dy : rows - pad + dy, pad + dx : cols - pad + dx]
            window = window - window.mean()
            denom = core_norm * float(np.sqrt((window * window).sum()))
            if denom <= 1e-9:
                continue
            score = float((core * window).sum()) / denom
            if score > best[1]:
                best = ((dx, dy), score)
    return best


def detect_cloud_shadow_drift(
    capture: Capture,
    *,
    contrast_threshold: float = 0.012,
    delta_energy_threshold: float = 0.0025,
    correlation_threshold: float = 0.45,
    max_shift: int = 6,
    max_evidence: int = 3,
) -> Detection:
    """TEMPORAL: a spatial luma-darkening field over terrain that ADVANCES.

    Two INDEPENDENT measurements are taken on the cloud-scale band and reported
    separately, because one number alone cannot separate the failure modes:

    ``band_pass_contrast``   absolute spatial contrast of the band in frame A.
                             Terrain relief lives in this band too, so contrast
                             alone does NOT prove a cloud contribution exists.
    ``temporal_delta_energy`` std of (band_B - band_A). Only a contribution that
                             actually changes between the two frames can raise
                             this; static terrain relief cancels exactly.

    Verdicts:

    MISSING       -> contrast below floor: no blotch-scale structure at all.
    PRESENT_WEAK  -> contrast passes but delta energy is below its floor. The
                     band is carried by structure that did not change. This
                     capture CANNOT tell "cloud contribution frozen" from
                     "cloud contribution too weak to register above the static
                     terrain relief". RED, but a weaker claim than STATIC.
    PRESENT_STATIC-> contrast and delta energy both pass, yet the correlation
                     peak sits at (0,0): a field that demonstrably changes but
                     does not advance. RED.
    PRESENT_MOVING-> contrast and delta energy pass and the peak sits at a
                     NON-zero shift with correlation above threshold.
    """

    try:
        import numpy as np
    except ImportError:  # pragma: no cover - environment guard
        return Detection(0, [], insufficient="numpy unavailable")
    frames = list(capture.frames)
    if len(frames) < 2:
        return Detection(
            0, [], insufficient="needs >= 2 PNG frames for a temporal comparison"
        )
    first, last = frames[0], frames[-1]
    if (
        first.frame_index is not None
        and last.frame_index is not None
        and first.frame_index == last.frame_index
    ):
        return Detection(
            0, [], insufficient="frames are not time-separated (identical frame_index)"
        )
    cells_capture = capture.primary_cells
    cols = cells_capture.cols if cells_capture and cells_capture.cols > 8 else 180
    rows = cells_capture.rows if cells_capture and cells_capture.rows > 8 else 113
    try:
        grid_a = _frame_luma_grid(first.path, cols, rows)
        grid_b = _frame_luma_grid(last.path, cols, rows)
    except ImportError:
        return Detection(0, [], insufficient="Pillow unavailable for PNG frame decode")
    except OSError as exc:
        return Detection(0, [], insufficient=f"frame decode failed: {exc}")
    # Terrain band only: drop the sky (upper rows) so moving clouds themselves
    # cannot masquerade as their own ground shadow.
    horizon = int(rows * 0.35)
    band_a = _cloud_band_pass(grid_a)[horizon:, :]
    band_b = _cloud_band_pass(grid_b)[horizon:, :]
    lit = grid_a[horizon:, :] > 0.02
    if lit.mean() < 0.05:
        return Detection(
            0,
            [],
            insufficient="terrain band is effectively black; no luma signal to read",
        )
    contrast = float(band_a[lit].std())
    # Static structure in the band (terrain relief, material tone) cancels in
    # the frame difference, so this measures ONLY the part of the band that
    # changed. It is what separates a frozen field from an absent contribution.
    delta_energy = float((band_b - band_a)[lit].std())
    delta_ratio = (delta_energy / contrast) if contrast > 1e-9 else 0.0
    shift, score = _best_shift(band_a, band_b, max_shift)
    detail = {
        "frames": [str(first.path), str(last.path)],
        "frame_index_pair": [first.frame_index, last.frame_index],
        "frame_time_seconds_pair": [first.frame_time_seconds, last.frame_time_seconds],
        "grid": [cols, rows],
        "horizon_row": horizon,
        "band_fine_cells": CLOUD_BAND_FINE_CELLS,
        "band_coarse_cells": CLOUD_BAND_COARSE_CELLS,
        "band_pass_contrast": round(contrast, 6),
        "contrast_threshold": contrast_threshold,
        "temporal_delta_energy": round(delta_energy, 6),
        "delta_energy_threshold": delta_energy_threshold,
        "delta_over_contrast_ratio": round(delta_ratio, 4),
        "best_shift_cells": list(shift),
        "correlation_peak": round(score, 4),
        "correlation_threshold": correlation_threshold,
    }
    evidence = [
        f"{first.path.name} -> {last.path.name} "
        f"contrast={contrast:.4f} delta_energy={delta_energy:.4f} "
        f"best_shift={shift} corr={score:.3f}"
    ][:max_evidence]
    if contrast < contrast_threshold:
        return Detection(0, evidence, detail=detail)
    if delta_energy < delta_energy_threshold:
        # The band exists but nothing in it moved. Whether the cloud field is
        # frozen or simply never contributed is NOT decidable from this pair.
        return Detection(
            count=1, evidence=evidence, detail=detail, present_verdict=V_PRESENT_WEAK
        )
    moving = shift != (0, 0) and score >= correlation_threshold
    return Detection(
        count=1,
        evidence=evidence,
        detail=detail,
        present_verdict=V_PRESENT_MOVING if moving else V_PRESENT_STATIC,
    )


def detect_glyph_phase_variation(
    capture: Capture,
    *,
    glyph_ids: Iterable[int],
    owner_kinds: Sequence[str] | None,
    min_changed_fraction: float = 0.02,
    max_evidence: int = 3,
) -> Detection:
    """TEMPORAL over two cells.jsonl captures: do the animated glyphs change?"""

    if len(capture.cell_captures) < 2:
        base = detect_glyph_set(
            capture,
            glyph_ids=glyph_ids,
            owner_kinds=owner_kinds,
            max_evidence=max_evidence,
        )
        if base.insufficient is None:
            base.detail["temporal"] = (
                "single capture: spatial presence only, phase variation NOT measured "
                "(supply a second --cells/--capture for the temporal check)"
            )
        return base
    wanted = frozenset(glyph_ids)
    owners = tuple(owner_kinds) if owner_kinds else None
    first, second = capture.cell_captures[0], capture.cell_captures[1]
    shared = 0
    changed = 0
    evidence: list[str] = []
    present = 0
    for key, cell in first.by_xy.items():
        if cell.glyph_id is None or cell.glyph_id not in wanted:
            continue
        if owners is not None and cell.owner_kind not in owners:
            continue
        present += 1
        other = second.by_xy.get(key)
        if other is None or other.glyph_id is None:
            continue
        shared += 1
        if other.glyph_id != cell.glyph_id:
            changed += 1
            if len(evidence) < max_evidence:
                evidence.append(
                    _locator(
                        first, cell, f"-> glyph_id={other.glyph_id} in {second.path.name}"
                    )
                )
    fraction = (changed / shared) if shared else 0.0
    detail = {
        "spatial_cells": present,
        "compared_cells": shared,
        "changed_cells": changed,
        "changed_fraction": round(fraction, 5),
        "min_changed_fraction": min_changed_fraction,
        "second_capture": str(second.path),
    }
    if present == 0:
        return Detection(0, evidence, detail=detail)
    verdict = V_PRESENT if fraction >= min_changed_fraction else V_PRESENT_STATIC
    return Detection(
        count=present, evidence=evidence, detail=detail, present_verdict=verdict
    )


def detect_png_bright_vertical_streak(
    capture: Capture,
    *,
    luma_threshold: float = 0.62,
    min_run: int = 4,
    max_evidence: int = 3,
) -> Detection:
    """PNG-only fallback: a pale, vertically elongated bright ribbon.

    This is a colour/geometry signature, NOT a glyph identity read. It cannot
    distinguish a waterfall from any other tall bright column and is only used
    when no cells.jsonl is available.
    """

    if not capture.frames:
        return Detection(0, [], insufficient="no PNG frames in capture")
    cells_capture = capture.primary_cells
    cols = cells_capture.cols if cells_capture and cells_capture.cols > 8 else 180
    rows = cells_capture.rows if cells_capture and cells_capture.rows > 8 else 113
    frame = capture.frames[0]
    try:
        grid = _frame_luma_grid(frame.path, cols, rows)
    except (ImportError, OSError) as exc:
        return Detection(0, [], insufficient=f"frame decode failed: {exc}")
    bright = grid >= luma_threshold
    runs = 0
    evidence: list[str] = []
    for x in range(cols):
        run = 0
        start = 0
        for y in range(rows):
            if bright[y, x]:
                if run == 0:
                    start = y
                run += 1
            else:
                if run >= min_run:
                    runs += 1
                    if len(evidence) < max_evidence:
                        evidence.append(
                            f"{frame.path}:grid_col={x} rows={start}..{start + run - 1} "
                            f"luma>={luma_threshold}"
                        )
                run = 0
        if run >= min_run:
            runs += 1
            if len(evidence) < max_evidence:
                evidence.append(
                    f"{frame.path}:grid_col={x} rows={start}..{start + run - 1} "
                    f"luma>={luma_threshold}"
                )
    return Detection(
        count=runs,
        evidence=evidence,
        detail={
            "frame": str(frame.path),
            "grid": [cols, rows],
            "luma_threshold": luma_threshold,
            "min_run": min_run,
            "bright_cell_fraction": round(float(bright.mean()), 5),
            "confidence": "low: colour/geometry proxy, no glyph identity",
        },
    )


def detect_png_sky_bright_anisotropy(
    capture: Capture,
    *,
    min_ratio: float = 1.35,
    max_evidence: int = 3,
) -> Detection:
    """PNG-only god-ray proxy: vertically elongated brightness in the sky band.

    God-ray light accumulation is captured into the FL-4359 witness rows
    (r5/r6 godray lit bits / sample counts), which cells.jsonl does NOT export.
    A luma-anisotropy proxy is therefore the only machine signature available.
    """

    if not capture.frames:
        return Detection(0, [], insufficient="no PNG frames in capture")
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        return Detection(0, [], insufficient="numpy unavailable")
    cells_capture = capture.primary_cells
    cols = cells_capture.cols if cells_capture and cells_capture.cols > 8 else 180
    rows = cells_capture.rows if cells_capture and cells_capture.rows > 8 else 113
    frame = capture.frames[0]
    try:
        grid = _frame_luma_grid(frame.path, cols, rows)
    except (ImportError, OSError) as exc:
        return Detection(0, [], insufficient=f"frame decode failed: {exc}")
    sky = grid[: max(4, int(rows * 0.45)), :]
    # God rays are THIN vertical shafts, so the fine-detail residual is the
    # correct filter here. This is deliberately the opposite polarity to
    # _cloud_band_pass, which needs the coarse blotch band.
    band = _detail_high_pass(sky)
    grad_y = float(np.abs(np.diff(band, axis=0)).mean())
    grad_x = float(np.abs(np.diff(band, axis=1)).mean())
    ratio = (grad_x / grad_y) if grad_y > 1e-6 else 0.0
    detail = {
        "frame": str(frame.path),
        "sky_rows": int(sky.shape[0]),
        "mean_abs_grad_x": round(grad_x, 6),
        "mean_abs_grad_y": round(grad_y, 6),
        "anisotropy_ratio_x_over_y": round(ratio, 4),
        "min_ratio": min_ratio,
        "confidence": "low: luma-anisotropy proxy; godray witness bits are not in "
        "cells.jsonl",
    }
    evidence = [f"{frame.path}: sky anisotropy x/y={ratio:.3f} (>= {min_ratio} => rays)"][
        :max_evidence
    ]
    return Detection(count=1 if ratio >= min_ratio else 0, evidence=evidence, detail=detail)


# ---------------------------------------------------------------------------
# Feature signature registry (DATA). Add an entry to register a new feature.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FeatureSignature:
    name: str
    summary: str
    # Ordered list of detectors; the first that is not INSUFFICIENT_INPUT wins.
    detectors: tuple[tuple[Callable[..., Detection], dict[str, Any]], ...]
    # (expected: bool | None, reason: str). None == not derivable from capture.
    expected_when: Callable[[Gates, Capture], tuple[bool | None, str]]
    provenance: str
    inputs: tuple[str, ...]
    # False when the signature is known to collide with ordinary paint in the
    # available input format. Such a feature still reports its verdict and count,
    # but it must not fail --strict, because a wolf-crying gate gets switched off
    # and then catches nothing at all. The reason belongs in CAPABILITY_TABLE.
    red_eligible: bool = True


def _has_owner(capture: Capture, kind: str) -> bool:
    primary = capture.primary_cells
    return bool(primary and primary.owner_counts.get(kind, 0) > 0)


def _gate_water_in_view(gates: Gates, capture: Capture) -> tuple[bool | None, str]:
    if capture.primary_cells is None:
        return None, "no cells.jsonl: cannot tell whether water is in frame"
    if not _has_owner(capture, "water"):
        return False, "no water-owner cells in this capture (nothing to break on)"
    return True, "water-owner cells present in frame"


def _gate_water_activity(gates: Gates, capture: Capture) -> tuple[bool | None, str]:
    expected, reason = _gate_water_in_view(gates, capture)
    if expected is not True:
        return expected, reason
    if gates.flag("water_current_package") is False:
        return False, "feature_state.water_current_package is off"
    return True, reason + " and water_current_package is on"


def _gate_cloud_shadow(gates: Gates, capture: Capture) -> tuple[bool | None, str]:
    if gates.flag("clouds") is False:
        return False, "feature_state.clouds is off"
    if gates.flag("cloud_shadow_deep") is False:
        return False, "feature_state.cloud_shadow_deep is off"
    if gates.flag("night") is True:
        return False, "feature_state.night is on: no daylight cloud shadow expected"
    if not gates.feature_state:
        return None, "no feature_state available to settle the cloud/day gate"
    return True, "clouds + cloud_shadow_deep on and night off"


def _gate_flowers(gates: Gates, capture: Capture) -> tuple[bool | None, str]:
    if gates.flag("foliage_cards") is False:
        return False, "feature_state.foliage_cards is off"
    if gates.flag("grass_field") is False:
        return False, "feature_state.grass_field is off"
    if capture.primary_cells is None:
        return None, "no cells.jsonl: cannot tell whether grass terrain is in frame"
    if not _has_owner(capture, "terrain"):
        return False, "no terrain-owner cells in this capture"
    return True, "foliage_cards + grass_field on and terrain in frame"


def _gate_site_conditional(
    feature: str,
) -> Callable[[Gates, Capture], tuple[bool | None, str]]:
    def gate(gates: Gates, capture: Capture) -> tuple[bool | None, str]:
        return None, (
            f"{feature} is world-anchored; whether its site is inside this frame is "
            f"not derivable from the capture. Pass --expect {feature} for a pose "
            "that must show it."
        )

    return gate


def _gate_godrays(gates: Gates, capture: Capture) -> tuple[bool | None, str]:
    if gates.flag("night") is True:
        return False, "feature_state.night is on: no daylight god rays expected"
    return None, (
        "god rays depend on sun direction and occluder geometry in frame; not "
        "derivable from the capture. Pass --expect god_rays for a ray pose."
    )


def _gate_weather(kind: str) -> Callable[[Gates, Capture], tuple[bool | None, str]]:
    def gate(gates: Gates, capture: Capture) -> tuple[bool | None, str]:
        if gates.weather_kind_name is None:
            return None, "weather_kind_name not present in capture metadata"
        expected = gates.weather_kind_name == kind
        return expected, (
            f"weather_kind_name == {gates.weather_kind_name!r} (needs {kind!r})"
        )

    return gate


def _gate_actor_submerge(gates: Gates, capture: Capture) -> tuple[bool | None, str]:
    if gates.actor_water_contact:
        return True, "metadata actor_rows report actor_water_contact = true"
    if capture.metadata is None:
        return None, "no metadata.json: actor water contact unknown"
    return False, "no actor row reports actor_water_contact; half-sink not expected"


def _gate_sky_clouds(gates: Gates, capture: Capture) -> tuple[bool | None, str]:
    if gates.flag("clouds") is False:
        return False, "feature_state.clouds is off"
    primary = capture.primary_cells
    if primary is None:
        return None, "no cells.jsonl: cannot tell whether sky cells are in frame"
    sky_cells = sum(
        count
        for kind, count in primary.owner_counts.items()
        if kind not in ("terrain", "water", "actor_sprite")
    )
    if sky_cells == 0:
        return False, (
            "capture has no sky-owner cells (the current dump contract labels every "
            "row terrain/water/actor_sprite), so sky cloud bodies are not expected"
        )
    return True, "clouds on and sky-owner cells present"


def _gate_birds(gates: Gates, capture: Capture) -> tuple[bool | None, str]:
    flag = gates.flag("ascii_birds")
    if flag is None:
        return None, "feature_state.ascii_birds not present in capture"
    return flag, f"feature_state.ascii_birds == {flag}"


FEATURE_REGISTRY: tuple[FeatureSignature, ...] = (
    FeatureSignature(
        name="shoreline_breaks",
        summary="FL-4216 authored 8-direction x 4-stage shoreline break glyphs painted "
        "on water cells that touch land.",
        detectors=(
            (
                detect_glyph_set,
                {
                    "glyph_ids": FL4216_BREAK_GLYPH_IDS,
                    "owner_kinds": ("water",),
                    "require_neighbour_owner": ("terrain",),
                    "neighbour_radius": 2,
                },
            ),
        ),
        expected_when=_gate_water_in_view,
        provenance=GLYPH_PROVENANCE["FL4216_GLYPH_ID"],
        inputs=("cells_jsonl",),
    ),
    FeatureSignature(
        name="cloud_shadow_drift",
        summary="Blotch-scale luma darkening over terrain that ADVANCES between two "
        "time-separated frames.",
        detectors=((detect_cloud_shadow_drift, {}),),
        expected_when=_gate_cloud_shadow,
        provenance="temporal luma band-pass over the terrain band; "
        "cell_owner_cloud_shadow_density is NULL in the current dump contract",
        inputs=("frames_png_pair",),
    ),
    FeatureSignature(
        name="waterfall",
        summary="FL-4743 world-anchored falling water ribbon: S-row FL-4216 band "
        "glyphs stacked as a vertical run on water cells.",
        detectors=(
            (
                detect_vertical_run,
                {
                    "glyph_ids": WATERFALL_GLYPH_IDS,
                    "owner_kinds": ("water",),
                    "min_run": 3,
                },
            ),
            (detect_png_bright_vertical_streak, {}),
        ),
        expected_when=_gate_site_conditional("waterfall"),
        provenance=GLYPH_PROVENANCE["WATERFALL_GLYPH_DIR"]
        + "; writer tag "
        + GLYPH_PROVENANCE["waterfall_writer_bit"],
        inputs=("cells_jsonl", "frames_png"),
    ),
    FeatureSignature(
        name="flowers",
        summary="Authored flower glyph sets (dense_flowers / mid_flowers / flower_top) "
        "on terrain cells, painted in an authored flower hue family. Glyph "
        "identity alone is not visibility, so a flower glyph painted in the "
        "grass-dominant hue wedge reports PRESENT_WRONG_COLOR.",
        detectors=(
            (
                detect_glyph_set,
                {
                    "glyph_ids": FLOWER_GLYPH_IDS,
                    "owner_kinds": ("terrain",),
                    "hue_families": FLOWER_HUE_FAMILY_ALLOWLIST,
                },
            ),
        ),
        expected_when=_gate_flowers,
        provenance=GLYPH_PROVENANCE["flowers"]
        + "; hue read from final_cell_paint_buffer.fg_rgb, MEASURED 100% "
        "populated (21060/21060 rows) on the 2026-07-29 clone dump",
        inputs=("cells_jsonl",),
    ),
    FeatureSignature(
        name="water_activity",
        summary="Animated water surface glyph family; with two captures also checks "
        "that the animation phase actually varies.",
        detectors=(
            (
                detect_glyph_phase_variation,
                {
                    "glyph_ids": WATER_ACTIVITY_PHASE_GLYPH_IDS,
                    "owner_kinds": ("water",),
                },
            ),
        ),
        expected_when=_gate_water_activity,
        provenance=GLYPH_PROVENANCE["water_activity"],
        inputs=("cells_jsonl", "cells_jsonl_pair"),
    ),
    FeatureSignature(
        name="god_rays",
        summary="Daylight god-ray shafts. No per-cell witness in cells.jsonl; PNG "
        "luma-anisotropy proxy only.",
        detectors=((detect_png_sky_bright_anisotropy, {}),),
        expected_when=_gate_godrays,
        provenance="godray capture words live in the FL-4359 witness rows r5/r6 "
        "(cell_owner_product.glsl:462) and are not exported per cell",
        inputs=("frames_png",),
    ),
    FeatureSignature(
        name="rain_glyphs",
        summary="Rain blade glyph family painted during rain weather.",
        detectors=((detect_glyph_set, {"glyph_ids": RAIN_BLADE_GLYPH_IDS}),),
        expected_when=_gate_weather("rain"),
        provenance=GLYPH_PROVENANCE["rain_blades"],
        inputs=("cells_jsonl",),
    ),
    FeatureSignature(
        name="snow_fall_glyphs",
        summary="Falling-snow glyph family (* + . ,) painted during snow weather.",
        detectors=((detect_glyph_set, {"glyph_ids": SNOW_FALL_GLYPH_IDS}),),
        expected_when=_gate_weather("snow"),
        provenance=GLYPH_PROVENANCE["snow_fall"],
        inputs=("cells_jsonl",),
        # MEASURED on the real 2026-07-29 clear-weather dump: 253 hits, all of
        # them ',' (0x2C) and '.' (0x2E) terrain punctuation, zero actual snow.
        # The falling-snow family is plain ASCII that ordinary terrain paint
        # already uses, so this signature reports but never fails --strict.
        red_eligible=False,
    ),
    FeatureSignature(
        name="snow_sparkle",
        summary="Snow cover sparkle glyph 0xB7 on terrain inside the cover band. "
        "Expected ABSENT while weather is not snow; a non-zero count is an "
        "UNEXPECTED RE-APPEARANCE.",
        detectors=(
            (
                detect_glyph_set,
                {"glyph_ids": (SNOW_SPARKLE_GLYPH_ID,), "owner_kinds": ("terrain",)},
            ),
        ),
        expected_when=_gate_weather("snow"),
        provenance=GLYPH_PROVENANCE["snow_sparkle"],
        inputs=("cells_jsonl",),
    ),
    FeatureSignature(
        name="actor_submerge",
        summary="Actor half-sink: waterline clip / foot-sink facts on actor cells.",
        detectors=(
            (
                detect_fact_fields,
                {
                    "fields": (
                        "cell_owner_waterline_clip",
                        "cell_owner_actor_waterline_clip_threshold",
                        "cell_owner_actor_sink_foot_world",
                        "actor_foot_sink_world",
                        "actor_foot_sink_screen_delta_px",
                    )
                },
            ),
        ),
        expected_when=_gate_actor_submerge,
        provenance="cell_owner_product_ssbo actor sink fields; NULL under "
        "fact_readback_state == cell_owner_product_readback_retired",
        inputs=("cells_jsonl",),
    ),
    FeatureSignature(
        name="sky_cloud_bodies",
        summary="Sky cloud/celestial body glyphs 0xB0 / 0xB1 in the upper screen rows.",
        detectors=(
            (
                detect_glyph_set,
                {
                    "glyph_ids": SKY_CLOUD_BODY_GLYPH_IDS,
                    "top_row_fraction": 0.45,
                },
            ),
        ),
        expected_when=_gate_sky_clouds,
        provenance=GLYPH_PROVENANCE["sky_cloud_bodies"],
        inputs=("cells_jsonl",),
    ),
    FeatureSignature(
        name="birds",
        summary="FL-4187 ASCII birds. World geometry, so the signature is the "
        "bird_face dynamic-mover owner kind, not a glyph id.",
        detectors=(
            (detect_fact_fields, {"fields": ("dynamic_mover_owner_kind",)}),
            (detect_owner_kind, {"owner_kinds": ("bird_face", "bird")}),
        ),
        expected_when=_gate_birds,
        provenance=GLYPH_PROVENANCE["birds"],
        inputs=("cells_jsonl",),
    ),
)

REGISTRY_BY_NAME = {sig.name: sig for sig in FEATURE_REGISTRY}


# Honest per-feature capability matrix. "can" / "cannot" is about the INPUT
# FORMAT, not about whether the feature happens to be present.
CAPABILITY_TABLE: tuple[dict[str, str], ...] = (
    {
        "feature": "shoreline_breaks",
        "cells_jsonl": "CAN - final_cell_paint_buffer.glyph_id vs the FL-4216 table, "
        "restricted to water owner cells with a terrain neighbour.",
        "frames_png": "CANNOT - a PNG carries no glyph identity, and the break glyphs "
        "are the same water colour family as ordinary waves.",
        "notes": "The S-row ids overlap the waterfall; the water/terrain adjacency "
        "filter is what keeps the two apart.",
    },
    {
        "feature": "cloud_shadow_drift",
        "cells_jsonl": "PARTIAL - cell_owner_cloud_shadow_density is NULL under the "
        "current retired-readback dump contract, so only the paint luma survives; a "
        "single capture also cannot show motion.",
        "frames_png": "CAN - difference-of-box-low-pass luma over the terrain band "
        "(the CLOUD-scale band, not the glyph-scale residual), cross-correlated "
        "between two time-separated frames.",
        "notes": "Needs >= 2 frames. Two numbers are reported separately: absolute "
        "band contrast and the TEMPORAL DELTA energy of the same band. Contrast "
        "alone cannot prove a cloud contribution, because static terrain relief "
        "occupies the same band. Delta energy below its floor returns PRESENT_WEAK "
        "(RED, but explicitly undecided between frozen and too-weak); delta energy "
        "above the floor with a (0,0) correlation peak returns PRESENT_STATIC (RED).",
    },
    {
        "feature": "waterfall",
        "cells_jsonl": "CAN (glyph geometry) - S-row FL-4216 ids in a vertical run on "
        "water cells. CANNOT read the writer tag: PRODUCT_CONTRIBUTED_WATERFALL_BIT "
        "(bit 25) is never exported into a cells.jsonl row.",
        "frames_png": "WEAK - bright vertical streak proxy; cannot separate a waterfall "
        "from any other tall pale column.",
        "notes": "Site-conditional gate: use --expect waterfall for a waterfall pose.",
    },
    {
        "feature": "flowers",
        "cells_jsonl": "CAN - authored flower glyph ids on terrain owner cells, AND "
        "the paint hue of those cells via final_cell_paint_buffer.fg_rgb, which is "
        "MEASURED 100% populated (21060/21060 rows) on the 2026-07-29 clone dump. "
        "When a dump carries no fg_rgb the hue check is reported as SKIPPED rather "
        "than silently passing.",
        "frames_png": "CANNOT - a PNG has no glyph identity, so a pink pixel cannot be "
        "attributed to a flower glyph rather than to any other pink paint.",
        "notes": "Glyph 647 is shared with the FL-4216 SE stage-2 break; the terrain "
        "owner filter disambiguates. Flowers were once detected by glyph while being "
        "painted grass-coloured and therefore invisible, so glyph presence WITHOUT an "
        "authored hue family returns PRESENT_WRONG_COLOR (RED).",
    },
    {
        "feature": "water_activity",
        "cells_jsonl": "CAN spatially from one capture; CAN check phase variation only "
        "with two captures of the same pose.",
        "frames_png": "CANNOT - no glyph identity.",
        "notes": "Wake glyphs are excluded because a wake only exists while swimming.",
    },
    {
        "feature": "god_rays",
        "cells_jsonl": "CANNOT - godray lit bits / sample counts live in the FL-4359 "
        "witness rows (r5/r6), which cells.jsonl does not carry.",
        "frames_png": "WEAK - sky luma anisotropy proxy only.",
        "notes": "Lowest-confidence entry in the registry. Treat a verdict here as a "
        "hint, not proof.",
    },
    {
        "feature": "rain_glyphs",
        "cells_jsonl": "CAN - rain blade glyph ids anywhere on screen.",
        "frames_png": "CANNOT.",
        "notes": "Expected-absent while weather_kind_name != 'rain'.",
    },
    {
        "feature": "snow_fall_glyphs",
        "cells_jsonl": "CAN, with a caveat - 0x2A/0x2E are also used by the star band "
        "and the rain ripple, so a clear-weather hit needs a look before it is called "
        "a snow regression.",
        "frames_png": "CANNOT.",
        "notes": "Expected-absent while weather_kind_name != 'snow'. NOT red-eligible: "
        "measured on the real 2026-07-29 clear-weather dump this returns 253 hits that "
        "are all ',' and '.' terrain punctuation. Reported, never a --strict failure.",
    },
    {
        "feature": "snow_sparkle",
        "cells_jsonl": "CAN - glyph 0xB7 restricted to terrain owner cells, matching "
        "the shader's paint_kind == OWNER_TERRAIN branch.",
        "frames_png": "CANNOT.",
        "notes": "Expected-absent today; a non-zero count is UNEXPECTED_PRESENT (RED).",
    },
    {
        "feature": "actor_submerge",
        "cells_jsonl": "PARTIAL - the sink / waterline-clip fields exist in the row "
        "schema but are NULL under fact_readback_state == "
        "cell_owner_product_readback_retired, so a MISSING verdict here can mean the "
        "dump surface was retired rather than the feature regressing.",
        "frames_png": "CANNOT.",
        "notes": "Gated on metadata actor_rows[].actor_water_contact.",
    },
    {
        "feature": "sky_cloud_bodies",
        "cells_jsonl": "CAN when the dump labels sky cells; the current dump contract "
        "labels every row terrain/water/actor_sprite, so the gate reports GATED_OFF "
        "with that reason instead of a false MISSING.",
        "frames_png": "CANNOT.",
        "notes": "0xB0 is shared with the rain ripple outer stage.",
    },
    {
        "feature": "birds",
        "cells_jsonl": "PARTIAL - dynamic_mover_owner_kind is the only bird signature "
        "and it is NULL under the retired-readback contract.",
        "frames_png": "CANNOT.",
        "notes": "Gated on feature_state.ascii_birds.",
    },
)


# ---------------------------------------------------------------------------
# Verdict selection
# ---------------------------------------------------------------------------


def run_detectors(
    signature: FeatureSignature, capture: Capture, max_evidence: int
) -> tuple[Detection, str]:
    last: Detection | None = None
    for func, kwargs in signature.detectors:
        detection = func(capture, max_evidence=max_evidence, **kwargs)
        if detection.insufficient is None:
            return detection, func.__name__
        last = detection
    assert last is not None
    return last, signature.detectors[-1][0].__name__


def is_red(row: dict[str, Any]) -> bool:
    """A verdict only counts as RED when the expected-when gate actually settled.

    MISSING can only arise with expected=True and UNEXPECTED_PRESENT only with
    expected=False, so the guard matters for PRESENT_STATIC: a frozen field in a
    capture whose gate could not be settled (no feature_state, unknown pose) is
    reported but must not fail the build, or every ungated capture turns red.

    An explicit ``--expect`` is different from an ungated capture.  It is an
    operator assertion that the named feature must be observable in this input,
    so an insufficient capture is a fail-closed RED result while retaining the
    more precise INSUFFICIENT_INPUT verdict in the per-feature report.
    """

    if row.get("forced_expectation") and row["verdict"] in {
        V_INSUFFICIENT_INPUT,
        V_MISSING,
    }:
        return True
    return (
        row["verdict"] in RED_VERDICTS
        and row["expected"] is not None
        and row["red_eligible"]
    )


def resolve_verdict(expected: bool | None, detection: Detection) -> str:
    if detection.insufficient is not None:
        return V_INSUFFICIENT_INPUT
    if expected is None:
        return detection.present_verdict if detection.count > 0 else V_GATE_UNKNOWN
    if expected:
        return detection.present_verdict if detection.count > 0 else V_MISSING
    return V_UNEXPECTED_PRESENT if detection.count > 0 else V_GATED_OFF


def analyze(
    capture: Capture,
    *,
    expect: Sequence[str] = (),
    not_expect: Sequence[str] = (),
    max_evidence: int = 3,
) -> dict[str, Any]:
    gates = resolve_gates(capture)
    forced_true = set(expect)
    forced_false = set(not_expect)
    features: dict[str, Any] = {}
    for signature in FEATURE_REGISTRY:
        expected, reason = signature.expected_when(gates, capture)
        if signature.name in forced_true:
            expected, reason = True, "--expect override on the command line"
        elif signature.name in forced_false:
            expected, reason = False, "--not-expect override on the command line"
        detection, detector_name = run_detectors(signature, capture, max_evidence)
        verdict = resolve_verdict(expected, detection)
        features[signature.name] = {
            "verdict": verdict,
            "count": detection.count,
            "expected": expected,
            "forced_expectation": signature.name in forced_true,
            "expected_reason": reason,
            "red_eligible": signature.red_eligible,
            "detection_input": detector_name,
            "declared_inputs": list(signature.inputs),
            "evidence_locator": detection.evidence,
            "insufficient_reason": detection.insufficient,
            "signature_provenance": signature.provenance,
            "summary": signature.summary,
            "detail": detection.detail,
        }
    red = sorted(name for name, row in features.items() if is_red(row))
    primary = capture.primary_cells
    return {
        "schema": REPORT_SCHEMA,
        "scope": ANALYZER_SCOPE,
        "complete_feature_coverage": False,
        "complete_coverage_owner": COMPLETE_COVERAGE_OWNER,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "capture": {
            "roots": [str(p) for p in capture.roots],
            "metadata_json": str(capture.metadata_path) if capture.metadata_path else None,
            "cells_jsonl": [str(c.path) for c in capture.cell_captures],
            "cells_rows": [len(c.cells) for c in capture.cell_captures],
            "cell_grid": [primary.cols, primary.rows] if primary else None,
            "owner_kind_counts": primary.owner_counts if primary else None,
            "frames": [
                {
                    "path": str(f.path),
                    "frame_index": f.frame_index,
                    "frame_time_seconds": f.frame_time_seconds,
                }
                for f in capture.frames
            ],
            "git_head": (capture.metadata or {}).get("git_head"),
            "notes": capture.notes,
        },
        "session_gates": {
            "source": gates.source,
            "weather_kind_name": gates.weather_kind_name,
            "actor_water_contact": gates.actor_water_contact,
            "feature_state": gates.feature_state,
        },
        "features": features,
        "summary": {
            "registry_size": len(FEATURE_REGISTRY),
            "red": red,
            "red_count": len(red),
            "verdict_counts": _verdict_counts(features),
            "strict_would_fail": bool(red),
        },
        "capability_table": list(CAPABILITY_TABLE),
    }


def _verdict_counts(features: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in features.values():
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_capture(args: argparse.Namespace) -> Capture:
    capture = Capture()
    for root in args.capture or []:
        path = Path(root)
        if not path.is_dir():
            raise SystemExit(f"--capture path is not a directory: {path}")
        discover_capture(path, capture)
    for cells in args.cells or []:
        path = Path(cells)
        if not path.is_file():
            raise SystemExit(f"--cells path is not a file: {path}")
        capture.cell_captures.append(load_cells(path))
    if args.metadata:
        path = Path(args.metadata)
        if not path.is_file():
            raise SystemExit(f"--metadata path is not a file: {path}")
        capture.metadata = json.loads(path.read_text(encoding="utf-8"))
        capture.metadata_path = path
    for frame in args.frames or []:
        path = Path(frame)
        if not path.is_file():
            raise SystemExit(f"--frames path is not a file: {path}")
        capture.frames.append(load_frame(path))
    if not capture.roots and not capture.cell_captures and not capture.frames:
        raise SystemExit("nothing to analyze: pass --capture, --cells, or --frames")
    return capture


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"feature-presence report  ({report['generated_utc']})",
        f"  capture roots : {', '.join(report['capture']['roots']) or '(explicit files)'}",
        f"  cells.jsonl   : {', '.join(report['capture']['cells_jsonl']) or 'none'}",
        f"  frames        : {len(report['capture']['frames'])}",
        f"  gates from    : {report['session_gates']['source']}",
        "",
    ]
    width = max(len(name) for name in report["features"])
    for name, row in report["features"].items():
        mark = "RED " if is_red(row) else "    "
        lines.append(
            f"{mark}{name.ljust(width)}  {row['verdict']:<19} count={row['count']:<7} "
            f"expected={row['expected']}  via={row['detection_input']}"
        )
        reason = row["insufficient_reason"] or row["expected_reason"]
        lines.append(f"        {reason}")
    lines.append("")
    lines.append(
        f"  RED: {report['summary']['red_count']} -> "
        f"{', '.join(report['summary']['red']) or 'none'}"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Per-feature presence verdicts for a Y9 render capture. Turns a capture "
            "into a machine-parseable report so a zero-signature feature RED-flags."
        )
    )
    parser.add_argument(
        "--capture",
        action="append",
        help="capture directory (auto-discovers metadata.json, cells.jsonl, *.png). "
        "Repeat for a temporal pair.",
    )
    parser.add_argument("--cells", action="append", help="explicit cells.jsonl path")
    parser.add_argument("--metadata", help="explicit metadata.json path")
    parser.add_argument("--frames", nargs="+", help="explicit ordered PNG frame paths")
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        help="force a feature's expected-when gate to TRUE (site/pose knowledge)",
    )
    parser.add_argument(
        "--not-expect",
        action="append",
        default=[],
        dest="not_expect",
        help="force a feature's expected-when gate to FALSE",
    )
    parser.add_argument("--out", help="write the JSON report to this path")
    parser.add_argument("--max-evidence", type=int, default=3)
    parser.add_argument("--text", action="store_true", help="human summary on stdout")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 2 when any feature is MISSING, PRESENT_STATIC, PRESENT_WEAK, "
        "PRESENT_WRONG_COLOR, or UNEXPECTED_PRESENT, or when an explicit --expect "
        "lacks usable input (the proof-gate hook)",
    )
    parser.add_argument(
        "--list-registry",
        action="store_true",
        help="print the feature signature registry and capability table, then exit",
    )
    args = parser.parse_args(argv)

    for name in list(args.expect) + list(args.not_expect):
        if name not in REGISTRY_BY_NAME:
            parser.error(f"unknown feature {name!r}; known: {sorted(REGISTRY_BY_NAME)}")

    if args.list_registry:
        payload = {
            "schema": "feature_presence_registry/v1",
            "scope": ANALYZER_SCOPE,
            "complete_feature_coverage": False,
            "complete_coverage_owner": COMPLETE_COVERAGE_OWNER,
            "features": [
                {
                    "name": s.name,
                    "summary": s.summary,
                    "inputs": list(s.inputs),
                    "provenance": s.provenance,
                }
                for s in FEATURE_REGISTRY
            ],
            "capability_table": list(CAPABILITY_TABLE),
            "verdicts": {
                "red": sorted(RED_VERDICTS),
                "all": list(ALL_VERDICTS),
            },
        }
        json.dump(payload, sys.stdout, indent=2, sort_keys=False)
        sys.stdout.write("\n")
        return 0

    capture = build_capture(args)
    report = analyze(
        capture,
        expect=args.expect,
        not_expect=args.not_expect,
        max_evidence=args.max_evidence,
    )
    text = json.dumps(report, indent=2, sort_keys=False, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.text:
        sys.stdout.write(render_text(report) + "\n")
    elif not args.out:
        sys.stdout.write(text + "\n")
    if args.strict and report["summary"]["red"]:
        sys.stderr.write("STRICT FAIL: " + ", ".join(report["summary"]["red"]) + "\n")
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
