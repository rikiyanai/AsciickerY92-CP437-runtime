#!/usr/bin/env python3
"""TTY browser for raw XP layers plus semantic-dictionary inspection."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.cli_style import kv, sparkline
from scripts.pipeline.bundle_wizard import semantic_dict
from scripts.pipeline import xp_assets_browser_layer_2_only as layer2_browser

SPRITE_DIR = REPO_ROOT / "assets" / "sprites"
KEY_ESCAPE = "\x1b"
KEY_PAGEUP = "PAGEUP"
KEY_PAGEDOWN = "PAGEDOWN"
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


@dataclass(frozen=True)
class RawAsset:
    entry: layer2_browser.SpriteEntry
    xp: object
    sprite_type: str

    @property
    def layer_count(self) -> int:
        return len(self.xp.layers)


@dataclass(frozen=True)
class RawPreviewCell:
    glyph: int
    fg: tuple[int, int, int]
    bg: tuple[int, int, int]
    selected: bool = False


def _infer_sprite_type(name: str) -> str:
    lower = name.lower()
    if lower.startswith("wolack") or lower.startswith("attack-") or "attack" in lower:
        return "attack"
    if lower.startswith("plydie-") or "death" in lower or "corpse" in lower:
        return "plydie"
    if lower.startswith("wolfie"):
        return "wolfie"
    if lower.startswith("bigbee") and "attack" not in lower:
        return "bigbee"
    return "player"


def _load_raw_asset(entry: layer2_browser.SpriteEntry) -> RawAsset:
    return RawAsset(
        entry=entry,
        xp=layer2_browser._load_xp_quiet(entry.path),
        sprite_type=_infer_sprite_type(entry.name),
    )


def _default_layer_index(asset: RawAsset) -> int:
    return min(2, asset.layer_count - 1)


def _relative_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _frame_rect(
    meta: layer2_browser.SpriteMetadata,
    state: layer2_browser.BrowserState,
    tick: int,
) -> tuple[int, int, int, int, int, int]:
    atlas_idx, angle, frame_idx = layer2_browser._select_frame(meta, state, tick)
    fr_x = atlas_idx % meta.fr_num_x
    fr_y = atlas_idx // meta.fr_num_x
    x0 = fr_x * meta.fr_width
    y0 = fr_y * meta.fr_height
    return x0, y0, angle, frame_idx, meta.fr_width, meta.fr_height


def _explicit_frame_rect(
    meta: layer2_browser.SpriteMetadata,
    *,
    anim_index: int,
    frame_idx: int,
    angle: int,
) -> tuple[int, int, int, int, int, int, int]:
    if anim_index < 0 or anim_index >= len(meta.anim_lengths):
        raise ValueError(
            f"anim_index {anim_index} out of range for asset with {len(meta.anim_lengths)} animation groups"
        )
    anim_length = meta.anim_lengths[anim_index]
    if frame_idx < 0 or frame_idx >= anim_length:
        raise ValueError(
            f"frame_idx {frame_idx} out of range for anim {anim_index} with {anim_length} frames"
        )
    if angle < 0 or angle >= meta.angles:
        raise ValueError(f"angle {angle} out of range for asset with {meta.angles} angles")

    frame_base = sum(meta.anim_lengths[:anim_index])
    x = frame_base + frame_idx
    atlas_idx = x + angle * meta.fr_num_x
    fr_x = atlas_idx % meta.fr_num_x
    fr_y = atlas_idx // meta.fr_num_x
    x0 = fr_x * meta.fr_width
    y0 = fr_y * meta.fr_height
    return x0, y0, angle, frame_idx, meta.fr_width, meta.fr_height, anim_length


def _exact_selection_bounds(
    frame_w: int,
    frame_h: int,
    *,
    row_lo: int | None,
    row_hi: int | None,
    col_lo: int | None,
    col_hi: int | None,
) -> tuple[int, int, int, int]:
    provided = [row_lo, row_hi, col_lo, col_hi]
    if all(value is None for value in provided):
        return (0, frame_h - 1, 0, frame_w - 1)
    if any(value is None for value in provided):
        raise ValueError("row/col bounds require all of --row-lo --row-hi --col-lo --col-hi")

    assert row_lo is not None
    assert row_hi is not None
    assert col_lo is not None
    assert col_hi is not None
    if row_lo < 0 or row_hi < 0 or col_lo < 0 or col_hi < 0:
        raise ValueError("row/col bounds must be non-negative frame-relative coordinates")
    if row_lo > row_hi:
        raise ValueError(f"row_lo {row_lo} exceeds row_hi {row_hi}")
    if col_lo > col_hi:
        raise ValueError(f"col_lo {col_lo} exceeds col_hi {col_hi}")
    if row_hi >= frame_h:
        raise ValueError(f"row_hi {row_hi} out of range for frame height {frame_h}")
    if col_hi >= frame_w:
        raise ValueError(f"col_hi {col_hi} out of range for frame width {frame_w}")
    return (row_lo, row_hi, col_lo, col_hi)


def _region_atlas(frame_w: int, frame_h: int) -> list[dict[str, object]]:
    return semantic_dict._export_region_atlas(frame_w=frame_w, frame_h=frame_h)


def _selected_region(index: int, frame_w: int, frame_h: int) -> dict[str, object]:
    atlas = _region_atlas(frame_w, frame_h)
    return atlas[index % len(atlas)]


def _region_bounds(region: dict[str, object]) -> tuple[int, int, int, int]:
    rows = region["rows"]
    cols = region["cols"]
    return int(rows[0]), int(rows[1]), int(cols[0]), int(cols[1])


def _clip_region_to_frame(
    region: dict[str, object],
    frame_w: int,
    frame_h: int,
) -> tuple[int, int, int, int] | None:
    row_lo, row_hi, col_lo, col_hi = _region_bounds(region)
    row_lo = max(0, min(frame_h - 1, row_lo))
    row_hi = max(0, min(frame_h - 1, row_hi))
    col_lo = max(0, min(frame_w - 1, col_lo))
    col_hi = max(0, min(frame_w - 1, col_hi))
    if row_lo > row_hi or col_lo > col_hi:
        return None
    return row_lo, row_hi, col_lo, col_hi


def _frame_cells(
    asset: RawAsset,
    layer_index: int,
    state: layer2_browser.BrowserState,
    tick: int,
    region_index: int,
) -> tuple[list[list[RawPreviewCell]], dict[str, object]]:
    meta = asset.entry.meta
    x0, y0, angle, frame_idx, frame_w, frame_h = _frame_rect(meta, state, tick)
    layer = asset.xp.layers[layer_index]
    selected_region = _selected_region(region_index, frame_w, frame_h)
    clipped = _clip_region_to_frame(selected_region, frame_w, frame_h)
    rows: list[list[RawPreviewCell]] = []
    for local_y in range(frame_h):
        row: list[RawPreviewCell] = []
        for local_x in range(frame_w):
            glyph, fg, bg = layer.data[y0 + local_y][x0 + local_x]
            is_selected = False
            if clipped is not None:
                row_lo, row_hi, col_lo, col_hi = clipped
                is_selected = row_lo <= local_y <= row_hi and col_lo <= local_x <= col_hi
            row.append(RawPreviewCell(glyph=glyph, fg=fg, bg=bg, selected=is_selected))
        rows.append(row)
    return rows, {
        "anim_index": min(max(state.anim, 0), len(meta.anim_lengths) - 1),
        "angle": angle,
        "frame_idx": frame_idx,
        "frame_w": frame_w,
        "frame_h": frame_h,
        "sheet_col_origin": x0,
        "sheet_row_origin": y0,
        "selected_region": selected_region,
        "clipped_region": clipped,
    }


def _frame_cells_explicit(
    asset: RawAsset,
    layer_index: int,
    *,
    anim_index: int,
    frame_idx: int,
    angle: int,
) -> tuple[list[list[RawPreviewCell]], dict[str, object]]:
    if layer_index < 0 or layer_index >= asset.layer_count:
        raise ValueError(f"layer_index {layer_index} out of range for asset with {asset.layer_count} layers")

    meta = asset.entry.meta
    x0, y0, normalized_angle, normalized_frame, frame_w, frame_h, anim_length = _explicit_frame_rect(
        meta,
        anim_index=anim_index,
        frame_idx=frame_idx,
        angle=angle,
    )
    layer = asset.xp.layers[layer_index]
    rows: list[list[RawPreviewCell]] = []
    for local_y in range(frame_h):
        row: list[RawPreviewCell] = []
        for local_x in range(frame_w):
            glyph, fg, bg = layer.data[y0 + local_y][x0 + local_x]
            row.append(RawPreviewCell(glyph=glyph, fg=fg, bg=bg, selected=False))
        rows.append(row)
    return rows, {
        "anim_index": anim_index,
        "anim_length": anim_length,
        "angle": normalized_angle,
        "frame_idx": normalized_frame,
        "frame_w": frame_w,
        "frame_h": frame_h,
        "sheet_col_origin": x0,
        "sheet_row_origin": y0,
    }


def _resolve_sprite_entry(
    sprite: str,
    sprite_dir: Path,
) -> layer2_browser.SpriteEntry:
    candidate = Path(sprite).expanduser()
    if not candidate.is_absolute():
        direct = (sprite_dir / sprite).resolve()
        if direct.exists():
            candidate = direct
    if candidate.exists():
        xp = layer2_browser._load_xp_quiet(candidate)
        meta = layer2_browser._parse_metadata(xp)
        if meta is None:
            raise ValueError(f"{candidate} is not a valid browsable XP sprite")
        return layer2_browser.SpriteEntry(path=candidate.resolve(), name=candidate.name, meta=meta)

    for entry in layer2_browser.scan_sprite_entries(sprite_dir):
        if entry.name == sprite:
            return entry
    raise FileNotFoundError(f"sprite '{sprite}' not found in {sprite_dir}")


def _cell_payload(
    *,
    asset: RawAsset,
    layer_index: int,
    anim_index: int,
    frame_idx: int,
    angle: int,
    sheet_row_origin: int,
    sheet_col_origin: int,
    local_row: int,
    local_col: int,
) -> dict[str, object]:
    glyph, fg, bg = asset.xp.layers[layer_index].data[sheet_row_origin + local_row][sheet_col_origin + local_col]
    layer0_key_rgb = tuple(asset.xp.layers[0].data[sheet_row_origin + local_row][sheet_col_origin + local_col][2])
    engine_flags = semantic_dict._engine_cell_transparency_flags(
        (glyph, fg, bg),
        layer0_key_rgb=layer0_key_rgb,
    )
    return {
        "source_asset": asset.entry.name,
        "layer_index": layer_index,
        "anim_index": anim_index,
        "frame_idx": frame_idx,
        "angle": angle,
        "local_row": local_row,
        "local_col": local_col,
        "sheet_row": sheet_row_origin + local_row,
        "sheet_col": sheet_col_origin + local_col,
        "glyph_id": glyph,
        "glyph_char": _cp437_char(glyph),
        "fg_rgb": list(fg),
        "bg_rgb": list(bg),
        "layer0_key_rgb": list(layer0_key_rgb),
        "bg_matches_layer0_key": bool(engine_flags["bg_matches_layer0_key"]),
        "fg_matches_layer0_key": bool(engine_flags["fg_matches_layer0_key"]),
        "engine_bg_transparent": bool(engine_flags["engine_bg_transparent"]),
        "engine_fg_transparent": bool(engine_flags["engine_fg_transparent"]),
        "engine_visible": bool(engine_flags["engine_visible"]),
        "semantic_region_guess": semantic_dict.get_body_part_at(
            local_row,
            local_col,
            frame_w=asset.entry.meta.fr_width,
            frame_h=asset.entry.meta.fr_height,
        ),
    }


def _selection_semantic_guess(
    *,
    asset: RawAsset,
    layer_index: int,
    anim_index: int,
    frame_idx: int,
    angle: int,
    frame_w: int,
    frame_h: int,
    row_lo: int,
    row_hi: int,
    col_lo: int,
    col_hi: int,
    sheet_row_origin: int,
    sheet_col_origin: int,
) -> dict[str, object]:
    cells: list[tuple[int, tuple[int, int, int], tuple[int, int, int]]] = []
    layer0_key_rgbs: list[tuple[int, int, int]] = []
    for local_y in range(row_lo, row_hi + 1):
        for local_x in range(col_lo, col_hi + 1):
            glyph, fg, bg = asset.xp.layers[layer_index].data[sheet_row_origin + local_y][sheet_col_origin + local_x]
            cells.append((glyph, fg, bg))
            layer0_key_rgbs.append(tuple(asset.xp.layers[0].data[sheet_row_origin + local_y][sheet_col_origin + local_x][2]))

    return semantic_dict.identify(
        cells,
        local_y=row_lo,
        local_x=col_lo,
        sprite_type=asset.sprite_type,
        frame_idx=frame_idx,
        angle=angle,
        rect_w=col_hi - col_lo + 1,
        rect_h=row_hi - row_lo + 1,
        frame_w=frame_w,
        frame_h=frame_h,
        layer0_key_rgbs=layer0_key_rgbs,
    )


def _exact_dump_payload(
    asset: RawAsset,
    *,
    layer_index: int,
    anim_index: int,
    frame_idx: int,
    angle: int,
    row_lo: int | None,
    row_hi: int | None,
    col_lo: int | None,
    col_hi: int | None,
) -> dict[str, object]:
    frame_rows, info = _frame_cells_explicit(
        asset,
        layer_index,
        anim_index=anim_index,
        frame_idx=frame_idx,
        angle=angle,
    )
    frame_h = int(info["frame_h"])
    frame_w = int(info["frame_w"])
    selected_row_lo, selected_row_hi, selected_col_lo, selected_col_hi = _exact_selection_bounds(
        frame_w,
        frame_h,
        row_lo=row_lo,
        row_hi=row_hi,
        col_lo=col_lo,
        col_hi=col_hi,
    )
    cells: list[dict[str, object]] = []
    sheet_row_origin = int(info["sheet_row_origin"])
    sheet_col_origin = int(info["sheet_col_origin"])
    for local_row in range(selected_row_lo, selected_row_hi + 1):
        for local_col in range(selected_col_lo, selected_col_hi + 1):
            cells.append(
                _cell_payload(
                    asset=asset,
                    layer_index=layer_index,
                    anim_index=int(info["anim_index"]),
                    frame_idx=int(info["frame_idx"]),
                    angle=int(info["angle"]),
                    sheet_row_origin=sheet_row_origin,
                    sheet_col_origin=sheet_col_origin,
                    local_row=local_row,
                    local_col=local_col,
                )
            )

    selection_mode = "frame" if (
        selected_row_lo == 0
        and selected_row_hi == frame_h - 1
        and selected_col_lo == 0
        and selected_col_hi == frame_w - 1
    ) else "rect"
    payload: dict[str, object] = {
        "source_asset": asset.entry.name,
        "source_path": str(asset.entry.path),
        "sprite_type": asset.sprite_type,
        "layer_index": layer_index,
        "anim_index": int(info["anim_index"]),
        "anim_length": int(info["anim_length"]),
        "frame_idx": int(info["frame_idx"]),
        "angle": int(info["angle"]),
        "frame_w": frame_w,
        "frame_h": frame_h,
        "sheet_row_origin": sheet_row_origin,
        "sheet_col_origin": sheet_col_origin,
        "selection_mode": selection_mode,
        "selection_bounds": {
            "row_lo": selected_row_lo,
            "row_hi": selected_row_hi,
            "col_lo": selected_col_lo,
            "col_hi": selected_col_hi,
        },
        "cells": cells,
        "frame_preview_rows": [
            "".join(_cp437_char(cell.glyph) for cell in row)
            for row in frame_rows
        ],
    }
    payload["selection_semantic_guess"] = _selection_semantic_guess(
        asset=asset,
        layer_index=layer_index,
        anim_index=int(info["anim_index"]),
        frame_idx=int(info["frame_idx"]),
        angle=int(info["angle"]),
        frame_w=frame_w,
        frame_h=frame_h,
        row_lo=selected_row_lo,
        row_hi=selected_row_hi,
        col_lo=selected_col_lo,
        col_hi=selected_col_hi,
        sheet_row_origin=sheet_row_origin,
        sheet_col_origin=sheet_col_origin,
    )
    return payload


def _cp437_char(glyph: int) -> str:
    if glyph in (0, 32):
        return " "
    try:
        return bytes([glyph & 0xFF]).decode("cp437")
    except Exception:
        return "?"


def _style_raw_cell(cell: RawPreviewCell) -> str:
    parts = [
        f"\033[38;2;{cell.fg[0]};{cell.fg[1]};{cell.fg[2]}m",
        f"\033[48;2;{cell.bg[0]};{cell.bg[1]};{cell.bg[2]}m",
    ]
    if cell.selected:
        parts.append("\033[7m")
    parts.append(_cp437_char(cell.glyph))
    parts.append("\033[0m")
    return "".join(parts)


def _render_frame_lines(rows: list[list[RawPreviewCell]]) -> list[str]:
    return ["".join(_style_raw_cell(cell) for cell in row) for row in rows]


def _visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def _pad_visible(text: str, width: int) -> str:
    return text + (" " * max(0, width - _visible_len(text)))


def _box_preview_lines(lines: list[str]) -> list[str]:
    if not lines:
        return []
    inner_width = max(_visible_len(line) for line in lines)
    border = "+" + ("-" * (inner_width + 2)) + "+"
    boxed = [border]
    for line in lines:
        boxed.append(f"| {_pad_visible(line, inner_width)} |")
    boxed.append(border)
    return boxed


def _layout_preview_and_info(
    preview_lines: list[str],
    info_lines: list[str],
    *,
    terminal_cols: int,
    gap: int = 2,
) -> list[str]:
    if not preview_lines:
        return info_lines
    if not info_lines:
        return preview_lines

    preview_width = max(_visible_len(line) for line in preview_lines)
    info_width = max(len(line) for line in info_lines)
    if preview_width + gap + info_width > terminal_cols:
        return preview_lines + [""] + info_lines

    total = max(len(preview_lines), len(info_lines))
    body_lines: list[str] = []
    for idx_line in range(total):
        left = preview_lines[idx_line] if idx_line < len(preview_lines) else ""
        right = info_lines[idx_line] if idx_line < len(info_lines) else ""
        body_lines.append(f"{_pad_visible(left, preview_width)}{' ' * gap}{right}".rstrip())
    return body_lines


def _is_side_by_side_layout(
    preview_lines: list[str],
    info_lines: list[str],
    *,
    terminal_cols: int,
    gap: int = 2,
) -> bool:
    if not preview_lines or not info_lines:
        return False
    preview_width = max(_visible_len(line) for line in preview_lines)
    info_width = max(len(line) for line in info_lines)
    return preview_width + gap + info_width <= terminal_cols


def _visible_panel_window(
    panel_lines: list[str],
    scroll: int,
    available_rows: int,
) -> tuple[int, int, list[str]]:
    if available_rows <= 0:
        return max(0, scroll), max(0, len(panel_lines)), []
    max_scroll = max(0, len(panel_lines) - available_rows)
    clamped_scroll = min(max(scroll, 0), max_scroll)
    return clamped_scroll, max_scroll, panel_lines[clamped_scroll:clamped_scroll + available_rows]


def _column_header(frame_w: int, *, show_grid: bool) -> list[str]:
    tens = "".join(str((idx // 10) % 10) if idx >= 10 else " " for idx in range(frame_w))
    ones = "".join(str(idx % 10) for idx in range(frame_w))
    if show_grid:
        tens = " ".join(list(tens))
        ones = " ".join(list(ones))
    return [f"    {tens}", f"    {ones}"]


def _grid_separator(frame_w: int) -> str:
    return "   +" + "+".join("-" for _ in range(frame_w)) + "+"


def _render_frame_with_axes(
    rows: list[list[RawPreviewCell]],
    *,
    show_grid: bool,
) -> list[str]:
    if not rows:
        return []
    frame_h = len(rows)
    frame_w = len(rows[0])
    lines = _column_header(frame_w, show_grid=show_grid)
    if show_grid:
        lines.append(_grid_separator(frame_w))
        for idx, row in enumerate(rows):
            styled = "|".join(_style_raw_cell(cell) for cell in row)
            lines.append(f"{idx:02d}|{styled}|")
            lines.append(_grid_separator(frame_w))
        return lines

    for idx, row in enumerate(rows):
        lines.append(f"{idx:02d}  {''.join(_style_raw_cell(cell) for cell in row)}")
    return lines


def _render_loading_screen(
    current: int,
    total: int,
    *,
    current_name: str = "",
    accepted: int = 0,
    stage: str = "Scanning XP sheets",
) -> str:
    width = 28
    progress = current / max(total, 1)
    filled = max(1, int(round(progress * width)))
    values = [0.0] * max(0, width - filled) + [progress] * filled
    chart = sparkline(values, lo=0.0, hi=1.0)
    lines = [
        "Raw XP layer inspector",
        "",
        f"{stage}...",
        chart,
        f"scanned {current}/{total}  valid {accepted}",
    ]
    if current_name:
        lines.append(f"last: {current_name}")
    return "\033[H\033[2J" + "\r\n".join(lines)


def _scan_sprite_entries_with_loading(sprite_dir: Path) -> list[layer2_browser.SpriteEntry]:
    paths = sorted(sprite_dir.glob("*.xp"), key=lambda item: item.name.lower())
    if not paths:
        return []

    entries: list[layer2_browser.SpriteEntry] = []
    last_draw = 0.0
    sys.stdout.write(_render_loading_screen(0, len(paths)))
    sys.stdout.flush()
    for idx, path in enumerate(paths, start=1):
        try:
            xp = layer2_browser._load_xp_quiet(path)
            meta = layer2_browser._parse_metadata(xp)
        except Exception:
            meta = None
        if meta is not None:
            entries.append(layer2_browser.SpriteEntry(path=path, name=path.name, meta=meta))
        now = time.monotonic()
        if idx == len(paths) or (now - last_draw) >= 0.04:
            sys.stdout.write(
                _render_loading_screen(
                    idx,
                    len(paths),
                    current_name=path.name,
                    accepted=len(entries),
                )
            )
            sys.stdout.flush()
            last_draw = now
    return entries


def _semantic_payload(
    asset: RawAsset,
    layer_index: int,
    state: layer2_browser.BrowserState,
    tick: int,
    region_index: int,
) -> dict[str, object]:
    frame_rows, info = _frame_cells(asset, layer_index, state, tick, region_index)
    region = info["selected_region"]
    clipped = info["clipped_region"]
    if clipped is None:
        return {
            "region": region,
            "result": None,
            "frame_rows": frame_rows,
            "frame_info": info,
            "warning": "semantic atlas region falls outside this frame",
        }

    row_lo, row_hi, col_lo, col_hi = clipped
    cells: list[tuple[int, tuple[int, int, int], tuple[int, int, int]]] = []
    for local_y in range(row_lo, row_hi + 1):
        for local_x in range(col_lo, col_hi + 1):
            cell = frame_rows[local_y][local_x]
            cells.append((cell.glyph, cell.fg, cell.bg))

    result = semantic_dict.identify(
        cells,
        local_y=row_lo,
        local_x=col_lo,
        sprite_type=asset.sprite_type,
        frame_idx=int(info["frame_idx"]),
        angle=int(info["angle"]),
        rect_w=col_hi - col_lo + 1,
        rect_h=row_hi - row_lo + 1,
        frame_w=int(info["frame_w"]),
        frame_h=int(info["frame_h"]),
        region_name_hint=str(region["name"]),
    )
    warning = None
    return {
        "region": region,
        "result": result,
        "frame_rows": frame_rows,
        "frame_info": info,
        "warning": warning,
    }


def _asset_list_lines(
    entries: list[layer2_browser.SpriteEntry],
    index: int,
    max_lines: int,
) -> list[str]:
    if max_lines <= 0:
        return []
    start = max(0, index - max_lines // 2)
    end = min(len(entries), start + max_lines)
    start = max(0, end - max_lines)
    lines = [f"Assets {start + 1}-{end} of {len(entries)}"]
    for i in range(start, end):
        prefix = ">" if i == index else " "
        entry = entries[i]
        lines.append(f"{prefix} {i + 1:03d} {entry.name}  {_relative_path(entry.path)}")
    return lines[:max_lines]


def _region_lines(selected_index: int, *, frame_w: int, frame_h: int) -> list[str]:
    lines = ["Region atlas"]
    atlas = _region_atlas(frame_w, frame_h)
    for idx, region in enumerate(atlas):
        rows = region["rows"]
        cols = region["cols"]
        prefix = ">" if idx == selected_index else " "
        lines.append(
            f"{prefix} {region['name']:<12} rows {rows[0]}-{rows[1]}  cols {cols[0]}-{cols[1]}"
        )
    return lines


def _semantic_lines(payload: dict[str, object]) -> list[str]:
    region = payload["region"]
    result = payload["result"]
    lines = [
        "Semantic dictionary",
        f"selected region  {region['name']}",
    ]
    if payload["warning"]:
        lines.append(f"warning         {payload['warning']}")
    if result is None:
        lines.append("result          unavailable")
        return lines

    stats = result["stats"]
    lines.extend(
        kv(
            [
                ("selected_cells", f"rows {region['rows'][0]}-{region['rows'][1]}  cols {region['cols'][0]}-{region['cols'][1]}"),
                ("semantic_id", result["semantic_id"]),
                ("semantic_bits", ", ".join(result["semantic_bits"])),
                ("body_part", result["body_part"]),
                ("body_group", result["body_group"]),
                ("direction", result["direction"]),
                ("frame_role", result["frame_role"]),
                ("equipment", result["equipment"]),
                ("anim_state", result["anim_state"]),
                ("confidence", result["confidence"]),
                ("transparent_ratio", f"{stats['transparent_ratio']:.3f}"),
                ("dominant_color", stats["dominant_color"]),
                ("dominant_glyph", stats["dominant_glyph"]),
            ]
        ).splitlines()
    )
    return lines


def _frame_summary(
    asset: RawAsset,
    layer_index: int,
    state: layer2_browser.BrowserState,
    tick: int,
    *,
    panel_scroll: int,
    panel_max_scroll: int,
) -> list[str]:
    meta = asset.entry.meta
    _, angle, frame = layer2_browser._select_frame(meta, state, tick)
    anim = min(max(state.anim, 0), len(meta.anim_lengths) - 1)
    anim_length = meta.anim_lengths[anim]
    layer_role = "metadata" if layer_index == 0 else f"raw layer {layer_index}"
    return [
        "Raw XP layer inspector",
        f"{asset.entry.name}  {_relative_path(asset.entry.path)}",
        f"sprite_type  {asset.sprite_type}",
        (
            f"layer {layer_index}/{asset.layer_count - 1} ({layer_role})  "
            f"anim {anim + 1}/{len(meta.anim_lengths)}  "
            f"frame {frame + 1}/{anim_length}  "
            f"angle {angle + 1}/{meta.angles}  "
            f"yaw {int(state.yaw) % 360}"
        ),
        (
            "[q] quit  [←/→] sprite  [a/d] angle  [w/s] anim  [,/.] frame"
        ),
        "[space] autoplay  [j/k] +/-10  [z/x] layer  [r/f] region",
        f"[[/]] scroll text  [PgUp/PgDn] scroll text  [g] grid  panel {panel_scroll}/{panel_max_scroll}",
        state.status or " ",
    ]


def _compose_screen(
    entries: list[layer2_browser.SpriteEntry],
    index: int,
    asset: RawAsset,
    state: layer2_browser.BrowserState,
    layer_index: int,
    region_index: int,
    show_grid: bool,
    panel_scroll: int,
) -> str:
    cols, rows = shutil.get_terminal_size(fallback=(120, 32))
    tick = layer2_browser._time_tick()
    payload = _semantic_payload(asset, layer_index, state, tick, region_index)
    frame_lines = _box_preview_lines(
        _render_frame_with_axes(payload["frame_rows"], show_grid=show_grid)
    )
    panel_lines = _semantic_lines(payload)
    panel_lines.extend([""])
    panel_lines.extend(
        _region_lines(
            region_index,
            frame_w=int(payload["frame_info"]["frame_w"]),
            frame_h=int(payload["frame_info"]["frame_h"]),
        )
    )
    panel_lines.extend([""])
    panel_lines.extend(_asset_list_lines(entries, index, len(entries) + 1))

    side_by_side = _is_side_by_side_layout(frame_lines, panel_lines, terminal_cols=cols)
    if side_by_side:
        fixed_lines = _frame_summary(
            asset,
            layer_index,
            state,
            tick,
            panel_scroll=panel_scroll,
            panel_max_scroll=0,
        )
        clamped_scroll, max_scroll, visible_panel_lines = _visible_panel_window(
            panel_lines,
            panel_scroll,
            max(0, rows - len(fixed_lines) - 1),
        )
        fixed_lines = _frame_summary(
            asset,
            layer_index,
            state,
            tick,
            panel_scroll=clamped_scroll,
            panel_max_scroll=max_scroll,
        )
        body_lines = _layout_preview_and_info(frame_lines, visible_panel_lines, terminal_cols=cols)
        visible = (fixed_lines + [""] + body_lines)[: max(1, rows)]
        return "\033[H\033[2J" + "\r\n".join(visible)

    clamped_scroll, max_scroll, _ = _visible_panel_window(panel_lines, panel_scroll, 1)
    fixed_lines = _frame_summary(
        asset,
        layer_index,
        state,
        tick,
        panel_scroll=clamped_scroll,
        panel_max_scroll=max_scroll,
    )
    available_panel_rows = max(0, rows - len(fixed_lines) - len(frame_lines) - 2)
    clamped_scroll, max_scroll, visible_panel_lines = _visible_panel_window(
        panel_lines,
        panel_scroll,
        available_panel_rows,
    )
    fixed_lines = _frame_summary(
        asset,
        layer_index,
        state,
        tick,
        panel_scroll=clamped_scroll,
        panel_max_scroll=max_scroll,
    )
    visible = (fixed_lines + [""] + frame_lines + [""] + visible_panel_lines)[: max(1, rows)]
    return "\033[H\033[2J" + "\r\n".join(visible)


def _read_key(fd: int) -> str | None:
    if not layer2_browser.select.select([fd], [], [], 0.05)[0]:
        return None
    data = os.read(fd, 16).decode("utf-8", "ignore")
    if not data:
        return None
    if data.startswith("\x1b[D"):
        return layer2_browser.KEY_ARROW_LEFT
    if data.startswith("\x1b[C"):
        return layer2_browser.KEY_ARROW_RIGHT
    if data.startswith("\x1b[A"):
        return layer2_browser.KEY_ARROW_UP
    if data.startswith("\x1b[B"):
        return layer2_browser.KEY_ARROW_DOWN
    if data.startswith("\x1b[5~"):
        return KEY_PAGEUP
    if data.startswith("\x1b[6~"):
        return KEY_PAGEDOWN
    return data[0]


def _apply_key(
    asset: RawAsset,
    state: layer2_browser.BrowserState,
    layer_index: int,
    key: str,
    region_index: int,
    show_grid: bool,
    panel_scroll: int,
) -> tuple[bool, int | None, int | None, int | None, bool | None, int | None]:
    if key in {"q", "Q", KEY_ESCAPE, "\x03"}:
        return False, None, None, None, None, None
    if key == layer2_browser.KEY_ARROW_LEFT:
        return True, -1, None, None, None, 0
    if key == layer2_browser.KEY_ARROW_RIGHT:
        return True, 1, None, None, None, 0
    if key in {"j", "J"}:
        return True, -10, None, None, None, 0
    if key in {"k", "K"}:
        return True, 10, None, None, None, 0
    if key in {"[", KEY_PAGEUP}:
        next_scroll = max(0, panel_scroll - 5)
        state.status = f"panel scroll {next_scroll}"
        return True, None, None, None, None, next_scroll
    if key in {"]", KEY_PAGEDOWN}:
        next_scroll = panel_scroll + 5
        state.status = f"panel scroll {next_scroll}"
        return True, None, None, None, None, next_scroll
    if key in {"z", "Z"}:
        next_layer = (layer_index - 1) % asset.layer_count
        state.status = f"layer {next_layer}"
        return True, None, next_layer, None, None, 0
    if key in {"x", "X"}:
        next_layer = (layer_index + 1) % asset.layer_count
        state.status = f"layer {next_layer}"
        return True, None, next_layer, None, None, 0
    if key in {"r", "R"}:
        atlas = _region_atlas(asset.entry.meta.fr_width, asset.entry.meta.fr_height)
        next_region = (region_index - 1) % len(atlas)
        state.status = str(_selected_region(next_region, asset.entry.meta.fr_width, asset.entry.meta.fr_height)["name"])
        return True, None, None, next_region, None, 0
    if key in {"f", "F"}:
        atlas = _region_atlas(asset.entry.meta.fr_width, asset.entry.meta.fr_height)
        next_region = (region_index + 1) % len(atlas)
        state.status = str(_selected_region(next_region, asset.entry.meta.fr_width, asset.entry.meta.fr_height)["name"])
        return True, None, None, next_region, None, 0
    if key in {"g", "G"}:
        next_show_grid = not show_grid
        state.status = "grid on" if next_show_grid else "grid off"
        return True, None, None, None, next_show_grid, panel_scroll
    keep_running, delta = layer2_browser._apply_key(state, asset.entry.meta, key)
    if delta is not None or key in {
        "a",
        "A",
        "d",
        "D",
        "w",
        "W",
        "s",
        "S",
        ",",
        ".",
        layer2_browser.KEY_ARROW_UP,
        layer2_browser.KEY_ARROW_DOWN,
        layer2_browser.KEY_SPACE,
    }:
        return keep_running, delta, None, None, None, 0
    return keep_running, delta, None, None, None, panel_scroll


def run_raw_layer_browser(sprite_dir: Path = SPRITE_DIR) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("raw XP layer inspector requires a TTY", file=sys.stderr)
        return 1
    if layer2_browser.select is None or layer2_browser.termios is None or layer2_browser.tty is None:
        print("raw XP layer inspector requires POSIX termios support", file=sys.stderr)
        return 1

    cache: dict[Path, RawAsset] = {}

    def get_asset(index: int) -> RawAsset:
        entry = entries[index]
        cached = cache.get(entry.path)
        if cached is None:
            cached = _load_raw_asset(entry)
            cache[entry.path] = cached
        return cached

    entries: list[layer2_browser.SpriteEntry] = []
    index = 0
    asset: RawAsset | None = None
    state: layer2_browser.BrowserState | None = None
    layer_index = 0
    region_index = 0
    show_grid = False
    panel_scroll = 0
    redraw_pending = [True]

    def on_resize(*_: object) -> None:
        redraw_pending[0] = True

    old_sigwinch = signal.signal(signal.SIGWINCH, on_resize)
    fd = sys.stdin.fileno()
    old_settings = layer2_browser.termios.tcgetattr(fd)
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()

    try:
        entries = _scan_sprite_entries_with_loading(sprite_dir)
        if not entries:
            print("no valid .xp sprites found", file=sys.stderr)
            return 1

        sys.stdout.write(
            _render_loading_screen(
                1,
                1,
                current_name=entries[0].name,
                accepted=len(entries),
                stage="Loading first raw-layer asset",
            )
        )
        sys.stdout.flush()
        layer2_browser.tty.setraw(fd)
        time.sleep(0.06)
        asset = get_asset(index)
        state = layer2_browser.default_browser_state(asset.entry.meta)
        layer_index = _default_layer_index(asset)
        state.status = asset.sprite_type
        while True:
            assert asset is not None
            assert state is not None
            if redraw_pending[0] or state.autoplay:
                redraw_pending[0] = False
                sys.stdout.write(
                    _compose_screen(
                        entries,
                        index,
                        asset,
                        state,
                        layer_index,
                        region_index,
                        show_grid,
                        panel_scroll,
                    )
                )
                sys.stdout.flush()

            key = _read_key(fd)
            if key is None:
                continue

            keep_running, index_delta, new_layer_index, new_region_index, new_show_grid, new_panel_scroll = _apply_key(
                asset, state, layer_index, key, region_index, show_grid, panel_scroll
            )
            if not keep_running:
                break
            if index_delta is not None:
                index = layer2_browser._adjust_index(index, index_delta, len(entries))
                asset = get_asset(index)
                state = layer2_browser.default_browser_state(asset.entry.meta)
                layer_index = _default_layer_index(asset)
                state.status = _relative_path(asset.entry.path)
                panel_scroll = 0
            if new_layer_index is not None:
                layer_index = new_layer_index
            if new_region_index is not None:
                region_index = new_region_index
            if new_show_grid is not None:
                show_grid = new_show_grid
            if new_panel_scroll is not None:
                panel_scroll = new_panel_scroll
            redraw_pending[0] = True
    finally:
        layer2_browser.termios.tcsetattr(fd, layer2_browser.termios.TCSADRAIN, old_settings)
        sys.stdout.write("\033[?1049l\033[?25h\033[0m")
        sys.stdout.flush()
        signal.signal(signal.SIGWINCH, old_sigwinch)

    return 0


def run_exact_dump(
    *,
    sprite_dir: Path,
    sprite: str,
    layer_index: int,
    anim_index: int,
    frame_idx: int,
    angle: int,
    row_lo: int | None,
    row_hi: int | None,
    col_lo: int | None,
    col_hi: int | None,
    out_path: Path | None,
) -> int:
    entry = _resolve_sprite_entry(sprite, sprite_dir)
    asset = _load_raw_asset(entry)
    payload = _exact_dump_payload(
        asset,
        layer_index=layer_index,
        anim_index=anim_index,
        frame_idx=frame_idx,
        angle=angle,
        row_lo=row_lo,
        row_hi=row_hi,
        col_lo=col_lo,
        col_hi=col_hi,
    )
    encoded = json.dumps(payload, indent=2) + "\n"
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0


# ---------------------------------------------------------------------------
# Anchor review mode (U2-U6)
# ---------------------------------------------------------------------------

# 8 distinct ANSI background tints for region color overlay
_REGION_TINTS: list[tuple[int, int, int]] = [
    (200, 60, 60),    # red
    (60, 200, 60),    # lime
    (80, 80, 220),    # blue
    (200, 200, 60),   # yellow
    (200, 60, 200),   # magenta
    (60, 200, 200),   # cyan
    (220, 140, 60),   # orange
    (140, 80, 220),   # violet
]

_HALF_BLOCK_GLYPHS = {220, 221, 222, 223}


@dataclass
class AnchorReviewState:
    anchor_data: dict
    anchor_path: Path
    frame_w: int
    frame_h: int
    current_angle: int = 0
    num_angles: int = 8
    cursor_x: int = 0
    cursor_y: int = 0
    selected_cells: set = field(default_factory=set)
    rect_start: tuple[int, int] | None = None
    dirty: bool = False
    half_block_mode: bool = False
    status: str = ""
    quit_pending: bool = False
    # maps (angle, x, y) -> region_index for the current review state
    cell_assignments: dict = field(default_factory=dict)
    # maps (angle, x, y) -> (fg_region_idx, bg_region_idx) for half-block dual-role
    dual_assignments: dict = field(default_factory=dict)
    # track which angles have been visited (view-only navigation)
    visited_angles: set = field(default_factory=set)
    # track which angles have actual cell modifications (triggers save rebuild)
    dirty_angles: set = field(default_factory=set)
    # prompt state for new-region and half-block sub-prompts
    prompt_mode: str = ""  # "", "new_region", "bg_region"
    prompt_buffer: str = ""
    # pending fg assignment for half-block two-step
    pending_fg_region: int | None = None
    # autoplay: cycle through angles on timer
    autoplay: bool = False
    autoplay_last: float = 0.0  # monotonic time of last angle advance
    autoplay_interval: float = 0.8  # seconds between angle advances
    # focused region index for the region-only panel (None = show all)
    region_focus: int | None = None
    # animation frame navigation
    current_anim: int = 0
    current_frame: int = 0
    anim_lengths: list[int] = field(default_factory=lambda: [1])

    @property
    def region_color_map(self) -> dict[int, tuple[int, int, int]]:
        """Map region index -> tint color for the current angle."""
        return {i: _REGION_TINTS[i % len(_REGION_TINTS)] for i in range(self._region_count)}

    @property
    def _region_count(self) -> int:
        frame_key = str(self.current_angle)
        frame = self.anchor_data.get("frames", {}).get(frame_key, {})
        return len(frame.get("regions", []))

    def regions_at_angle(self, angle: int | None = None) -> list[dict]:
        if angle is None:
            angle = self.current_angle
        frame_key = str(angle)
        frame = self.anchor_data.get("frames", {}).get(frame_key, {})
        return frame.get("regions", [])

    def region_cell_count(self, angle: int, region_idx: int) -> int:
        count = 0
        for (a, _x, _y), ridx in self.cell_assignments.items():
            if a == angle and ridx == region_idx:
                count += 1
        return count


def _load_anchor_state(anchor_path: Path) -> AnchorReviewState:
    """Load anchor JSON and initialize review state.

    Validates required JSON shape on load. Raises ValueError if the anchor
    file is missing required keys, has wrong types, or has invalid frame data.
    """
    with open(anchor_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    # --- Anchor JSON shape validation ---
    required_top = ["frame_w", "frame_h", "grid_layout", "frames"]
    for key in required_top:
        if key not in data:
            raise ValueError(f"anchor JSON missing required top-level key: '{key}'")

    frame_w = data["frame_w"]
    frame_h = data["frame_h"]
    if not isinstance(frame_w, int) or frame_w <= 0:
        raise ValueError(f"frame_w must be a positive integer, got {frame_w!r}")
    if not isinstance(frame_h, int) or frame_h <= 0:
        raise ValueError(f"frame_h must be a positive integer, got {frame_h!r}")

    gl = data.get("grid_layout", {})
    if not isinstance(gl, dict):
        raise ValueError("grid_layout must be an object")
    num_angles = gl.get("angles", 8)
    if not isinstance(num_angles, int) or num_angles <= 0:
        raise ValueError(f"grid_layout.angles must be a positive integer, got {num_angles!r}")

    frames = data.get("frames", {})
    if not isinstance(frames, dict):
        raise ValueError("frames must be an object")

    for frame_key, frame_data in frames.items():
        if not isinstance(frame_data, dict):
            raise ValueError(f"frames.{frame_key} must be an object")
        regions = frame_data.get("regions")
        if not isinstance(regions, list):
            raise ValueError(f"frames.{frame_key}.regions must be a list")
        for ridx, region in enumerate(regions):
            if not isinstance(region, dict):
                raise ValueError(f"frames.{frame_key}.regions[{ridx}] must be an object")
            for rk in ("name", "bbox", "confidence", "palette_roles"):
                if rk not in region:
                    raise ValueError(
                        f"frames.{frame_key}.regions[{ridx}] missing key: '{rk}'"
                    )
            if not isinstance(region.get("name"), str):
                raise ValueError(
                    f"frames.{frame_key}.regions[{ridx}].name must be a string"
                )
            bbox = region.get("bbox")
            if not (isinstance(bbox, list) and len(bbox) == 4
                    and all(isinstance(v, int) for v in bbox)):
                raise ValueError(
                    f"frames.{frame_key}.regions[{ridx}].bbox must be [int, int, int, int]"
                )

    anim_lengths = gl.get("anim_counts", [1])
    if not isinstance(anim_lengths, list) or not anim_lengths:
        anim_lengths = [1]

    st = AnchorReviewState(
        anchor_data=data,
        anchor_path=anchor_path,
        frame_w=frame_w,
        frame_h=frame_h,
        num_angles=num_angles,
        anim_lengths=anim_lengths,
    )

    # Build cell_assignments from existing semantic_cells
    for frame_key, frame_data in data.get("frames", {}).items():
        angle = frame_data.get("angle", int(frame_key))
        for ridx, region in enumerate(frame_data.get("regions", [])):
            for cell in region.get("semantic_cells", []):
                st.cell_assignments[(angle, cell["x"], cell["y"])] = ridx

    # Build dual_assignments from existing fg_region/bg_region fields
    for frame_key, frame_data in data.get("frames", {}).items():
        angle = frame_data.get("angle", int(frame_key))
        regions = frame_data.get("regions", [])
        # Map region name -> index for resolving fg_region/bg_region values
        region_name_to_idx: dict[str, int] = {}
        for ridx, region in enumerate(regions):
            region_name_to_idx[region["name"]] = ridx
        for ridx, region in enumerate(regions):
            for cell in region.get("semantic_cells", []):
                fg_region_name = cell.get("fg_region")
                bg_region_name = cell.get("bg_region")
                if fg_region_name or bg_region_name:
                    fg_ridx = region_name_to_idx.get(fg_region_name) if fg_region_name else None
                    bg_ridx = region_name_to_idx.get(bg_region_name) if bg_region_name else None
                    if fg_ridx is not None or bg_ridx is not None:
                        # When only one side has a dual-region value, the other
                        # side defaults to the cell's containing region index.
                        # This is correct for half-block cells where e.g.
                        # fg_region="shirt" with no bg_region means bg still
                        # belongs to the cell's parent region.
                        st.dual_assignments[(angle, cell["x"], cell["y"])] = (
                            fg_ridx if fg_ridx is not None else ridx,
                            bg_ridx if bg_ridx is not None else ridx,
                        )

    st.status = f"Loaded {anchor_path.name} ({num_angles} angles, {frame_w}x{frame_h})"
    return st


def _anchor_cell_region_index(st: AnchorReviewState, angle: int, x: int, y: int) -> int | None:
    """Return region index for a cell, or None if unassigned."""
    return st.cell_assignments.get((angle, x, y))


def _style_anchor_cell(
    glyph: int,
    fg: tuple[int, int, int],
    bg: tuple[int, int, int],
    *,
    region_tint: tuple[int, int, int] | None,
    is_cursor: bool,
    is_selected: bool,
) -> str:
    """Render a single cell with optional region tint, cursor, and selection indicators."""
    parts: list[str] = []

    # Blend region tint into background
    if region_tint is not None:
        # 50% blend of region tint with actual bg
        blend_bg = (
            (bg[0] + region_tint[0]) // 2,
            (bg[1] + region_tint[1]) // 2,
            (bg[2] + region_tint[2]) // 2,
        )
    else:
        blend_bg = bg

    parts.append(f"\033[38;2;{fg[0]};{fg[1]};{fg[2]}m")
    parts.append(f"\033[48;2;{blend_bg[0]};{blend_bg[1]};{blend_bg[2]}m")

    if is_cursor and is_selected:
        parts.append("\033[1;4;7m")  # bold + underline + inverted
    elif is_cursor:
        parts.append("\033[1;4m")  # bold + underline
    elif is_selected:
        parts.append("\033[7m")  # inverted

    parts.append(_cp437_char(glyph))
    parts.append("\033[0m")
    return "".join(parts)


def _anchor_render_frame(
    st: AnchorReviewState,
    cell_data: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]],
) -> list[str]:
    """Render the frame with region tints, cursor, and selection overlay."""
    lines: list[str] = []
    # Column header
    tens = "".join(str((i // 10) % 10) if i >= 10 else " " for i in range(st.frame_w))
    ones = "".join(str(i % 10) for i in range(st.frame_w))
    lines.append(f"    {tens}")
    lines.append(f"    {ones}")

    color_map = st.region_color_map
    angle = st.current_angle

    for y in range(st.frame_h):
        row_chars: list[str] = []
        for x in range(st.frame_w):
            glyph, fg, bg = cell_data[y][x]
            ridx = _anchor_cell_region_index(st, angle, x, y)
            tint = color_map.get(ridx) if ridx is not None else None
            is_cursor = (x == st.cursor_x and y == st.cursor_y)
            is_selected = (x, y) in st.selected_cells
            row_chars.append(_style_anchor_cell(
                glyph, fg, bg,
                region_tint=tint,
                is_cursor=is_cursor,
                is_selected=is_selected,
            ))
        lines.append(f"{y:02d}  {''.join(row_chars)}")
    return lines


def _anchor_render_region_only(
    st: AnchorReviewState,
    cell_data: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]],
) -> list[str]:
    """Render only cells belonging to the focused region, rest as dots."""
    lines: list[str] = []
    focus = st.region_focus
    color_map = st.region_color_map
    angle = st.current_angle
    regions = st.regions_at_angle()
    title = f"Region: {regions[focus]['name']}" if focus is not None and 0 <= focus < len(regions) else "Region: (none)"
    lines.append(title)

    for y in range(st.frame_h):
        row_chars: list[str] = []
        for x in range(st.frame_w):
            is_cursor = (x == st.cursor_x and y == st.cursor_y)
            ridx = _anchor_cell_region_index(st, angle, x, y)
            if focus is not None and ridx == focus:
                glyph, fg, bg = cell_data[y][x]
                tint = color_map.get(ridx, (128, 128, 128))
                cursor_attr = "\033[1;4m" if is_cursor else ""
                row_chars.append(f"{cursor_attr}\033[38;2;{fg[0]};{fg[1]};{fg[2]}m\033[48;2;{tint[0]};{tint[1]};{tint[2]}m{_cp437_char(glyph)}\033[0m")
            elif is_cursor:
                row_chars.append("\033[1;4m.\033[0m")
            else:
                row_chars.append("\033[2m.\033[0m")
        lines.append(f"{y:02d}  {''.join(row_chars)}")
    return lines


def _anchor_render_uv_map(
    st: AnchorReviewState,
    asset: object,
    layer_index: int,
) -> list[str]:
    """Render UV coordinate map showing atlas-global (col,row) for each frame cell."""
    lines: list[str] = []
    meta = asset.entry.meta
    angle = st.current_angle
    frame_base = sum(meta.anim_lengths[:st.current_anim]) + st.current_frame
    atlas_idx = frame_base + angle * meta.fr_num_x
    fr_x = atlas_idx % meta.fr_num_x
    fr_y = atlas_idx // meta.fr_num_x
    x0 = fr_x * meta.fr_width
    y0 = fr_y * meta.fr_height

    lines.append(f"UV map (atlas offset {x0},{y0})")
    # Column header showing local x
    hdr = " " * 4 + " ".join(f"{x:^5}" for x in range(st.frame_w))
    lines.append(hdr)
    for y in range(st.frame_h):
        row_parts: list[str] = []
        for x in range(st.frame_w):
            gx = x0 + x
            gy = y0 + y
            # Show atlas-global coords; cursor cell highlighted
            cell_str = f"{gx:>2},{gy:<2}"
            if x == st.cursor_x and y == st.cursor_y:
                row_parts.append(f"\033[1;7m{cell_str}\033[0m")
            else:
                row_parts.append(cell_str)
        lines.append(f"{y:02d}  {' '.join(row_parts)}")
    return lines


def _anchor_region_panel(st: AnchorReviewState) -> list[str]:
    """Side panel showing regions at current angle with cell counts."""
    regions = st.regions_at_angle()
    lines = [f"Regions at angle {st.current_angle}"]
    color_map = st.region_color_map
    for idx, region in enumerate(regions):
        tint = color_map.get(idx, (128, 128, 128))
        count = st.region_cell_count(st.current_angle, idx)
        conf = region.get("confidence", "?")
        swatch = f"\033[48;2;{tint[0]};{tint[1]};{tint[2]}m  \033[0m"
        lines.append(f" {idx + 1}. {swatch} {region['name']:<12} ({count} cells, {conf})")
    lines.append("")
    lines.append(f"Unassigned: {_count_unassigned(st)} cells")
    return lines


def _count_unassigned(st: AnchorReviewState) -> int:
    """Count cells not assigned to any region at this angle."""
    assigned = {(x, y) for (a, x, y) in st.cell_assignments if a == st.current_angle}
    return (st.frame_w * st.frame_h) - len(assigned)


def _anchor_status_bar(st: AnchorReviewState) -> list[str]:
    """Build status bar lines for anchor review mode."""
    lines: list[str] = []
    # Cursor info
    ridx = _anchor_cell_region_index(st, st.current_angle, st.cursor_x, st.cursor_y)
    region_name = "unassigned"
    if ridx is not None:
        regions = st.regions_at_angle()
        if 0 <= ridx < len(regions):
            region_name = regions[ridx]["name"]
    anim_len = st.anim_lengths[st.current_anim]
    frame_info = f"angle {st.current_angle}  anim {st.current_anim} frame {st.current_frame}/{anim_len}"
    cursor_info = f"cursor ({st.cursor_x},{st.cursor_y}) region={region_name}  sel={len(st.selected_cells)}"
    hb_indicator = "  [H-BLOCK ON]" if st.half_block_mode else ""
    dirty_indicator = "  [MODIFIED]" if st.dirty else ""
    play_indicator = "  [PLAY]" if st.autoplay else ""
    lines.append(f"{frame_info}  {cursor_info}{hb_indicator}{dirty_indicator}{play_indicator}")

    # Prompt line
    if st.prompt_mode == "new_region":
        lines.append(f"New region name: {st.prompt_buffer}_")
    elif st.prompt_mode == "bg_region":
        lines.append(f"bg region? [1-9 or n]: _")
    elif st.status:
        lines.append(st.status)
    else:
        lines.append(" ")

    return lines


def _anchor_help_lines() -> list[str]:
    return [
        "Anchor review",
        "[arrows] move cursor  [a/d] angle  [w/s] anim  [,/.] frame  [x] toggle  [m] rect",
        "[1-9] assign region  [n] new region  [Backspace] unassign  [h] half-block mode",
        "[r/f] cycle region focus  [p] autoplay angles  [Ctrl+S] save  [q] quit  [Esc] clear",
    ]


def _anchor_compose_screen(
    st: AnchorReviewState,
    cell_data: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]],
    asset: object | None = None,
    layer_index: int = 2,
) -> str:
    """Compose the full terminal output for anchor review mode (3-panel layout)."""
    cols, rows = shutil.get_terminal_size(fallback=(120, 32))

    help_lines = _anchor_help_lines()
    status_lines = _anchor_status_bar(st)

    # Panel 1: current view (sprite + region tint overlay)
    frame_lines = _anchor_render_frame(st, cell_data)
    box1 = _box_preview_lines(frame_lines)

    # Panel 2: region-only view (focused region isolated)
    region_lines = _anchor_render_region_only(st, cell_data)
    box2 = _box_preview_lines(region_lines)

    # Panel 3: UV coordinate map
    if asset is not None:
        uv_lines = _anchor_render_uv_map(st, asset, layer_index)
        box3 = _box_preview_lines(uv_lines)
    else:
        box3 = []

    # Region info panel (text list)
    panel_lines = _anchor_region_panel(st)

    # Layout: try to fit panels side-by-side
    # Compose box1 + box2 side-by-side first
    row1 = _layout_preview_and_info(box1, box2, terminal_cols=cols)
    # Then append box3 + region panel below or to the right
    if box3:
        row2 = _layout_preview_and_info(box3, panel_lines, terminal_cols=cols)
    else:
        row2 = panel_lines

    visible = (help_lines + [""] + row1 + [""] + row2 + [""] + status_lines)[:max(1, rows)]
    return "\033[H\033[2J" + "\r\n".join(visible)


def _recalculate_bbox(st: AnchorReviewState, angle: int, region_idx: int) -> list[int]:
    """Recalculate bounding box for a region from its member cells."""
    xs = []
    ys = []
    for (a, x, y), ridx in st.cell_assignments.items():
        if a == angle and ridx == region_idx:
            xs.append(x)
            ys.append(y)
    if not xs:
        return [0, 0, 0, 0]
    return [min(xs), min(ys), max(xs), max(ys)]


def _assign_cells_to_region(st: AnchorReviewState, region_idx: int) -> None:
    """Assign all selected cells to the given region index."""
    angle = st.current_angle
    for (x, y) in st.selected_cells:
        st.cell_assignments[(angle, x, y)] = region_idx
    st.dirty = True
    st.visited_angles.add(angle)
    st.dirty_angles.add(angle)
    regions = st.regions_at_angle()
    if 0 <= region_idx < len(regions):
        st.status = f"Assigned {len(st.selected_cells)} cells to '{regions[region_idx]['name']}'"


def _unassign_cells(st: AnchorReviewState) -> None:
    """Unassign all selected cells (remove from cell_assignments)."""
    angle = st.current_angle
    count = 0
    for (x, y) in st.selected_cells:
        key = (angle, x, y)
        if key in st.cell_assignments:
            del st.cell_assignments[key]
            count += 1
        # Also remove dual assignments
        if key in st.dual_assignments:
            del st.dual_assignments[key]
    if count > 0:
        st.dirty = True
        st.visited_angles.add(angle)
        st.dirty_angles.add(angle)
    st.status = f"Unassigned {count} cells"


def _create_new_region(st: AnchorReviewState, name: str) -> int:
    """Create a new region at the current angle and return its index."""
    frame_key = str(st.current_angle)
    frames = st.anchor_data.setdefault("frames", {})
    frame = frames.setdefault(frame_key, {
        "angle": st.current_angle,
        "regions": [],
    })
    regions = frame.setdefault("regions", [])

    # Check if name already exists -- assign to existing if so
    for idx, r in enumerate(regions):
        if r["name"] == name:
            return idx

    new_region = {
        "name": name,
        "bbox": [0, 0, 0, 0],
        "confidence": "medium",
        "palette_roles": [],
        "semantic_cells": [],
        "slot_affinity": "body",
        "notes": f"Created during anchor review at angle {st.current_angle}.",
    }
    regions.append(new_region)
    return len(regions) - 1


def _read_anchor_key(fd: int) -> str | None:
    """Read a key from the terminal, returning special key names for arrows."""
    if not layer2_browser.select.select([fd], [], [], 0.05)[0]:
        return None
    data = os.read(fd, 16).decode("utf-8", "ignore")
    if not data:
        return None
    if data.startswith("\x1b[A"):
        return "UP"
    if data.startswith("\x1b[B"):
        return "DOWN"
    if data.startswith("\x1b[C"):
        return "RIGHT"
    if data.startswith("\x1b[D"):
        return "LEFT"
    if data == "\x1b":
        return "ESCAPE"
    if data == "\x13":
        return "CTRL_S"
    if data in ("\x7f", "\x08"):
        return "BACKSPACE"
    if data == "\r" or data == "\n":
        return "ENTER"
    return data[0]


def _save_anchor(st: AnchorReviewState) -> str:
    """Save the anchor JSON atomically. Returns status message."""
    data = st.anchor_data

    # Rebuild all dirty angle frames from review state (view-only visited_angles do not rebuild)
    for angle in st.dirty_angles:
        frame_key = str(angle)
        frame = data.get("frames", {}).get(frame_key)
        if frame is None:
            continue

        regions = frame.get("regions", [])

        # Build a lookup of existing cell data across ALL regions at this angle
        # (fixes moved-cell corruption: a cell moved from one region to another
        #  still preserves its original glyph/color data)
        all_existing_cells: dict[tuple[int, int], dict] = {}
        for region in regions:
            for old_cell in region.get("semantic_cells", []):
                key = (old_cell["x"], old_cell["y"])
                if key not in all_existing_cells:
                    all_existing_cells[key] = old_cell

        for ridx, region in enumerate(regions):
            # Collect cells assigned to this region at this angle
            new_cells = []
            for (a, x, y), assigned_ridx in sorted(st.cell_assignments.items()):
                if a == angle and assigned_ridx == ridx:
                    # Preserve existing cell data across all regions at this angle
                    existing = all_existing_cells.get((x, y))
                    if existing is not None:
                        cell_entry = dict(existing)
                    else:
                        cell_entry = {
                            "x": x,
                            "y": y,
                            "glyph": 0,
                            "fg": "#000000",
                            "bg": "#000000",
                            "role": f"{region['name']}_cell",
                        }

                    # Apply dual-role if present
                    dual = st.dual_assignments.get((angle, x, y))
                    if dual is not None:
                        fg_ridx, bg_ridx = dual
                        fg_regions = st.regions_at_angle(angle)
                        if 0 <= fg_ridx < len(fg_regions):
                            cell_entry["fg_region"] = fg_regions[fg_ridx]["name"]
                        if 0 <= bg_ridx < len(fg_regions):
                            cell_entry["bg_region"] = fg_regions[bg_ridx]["name"]

                    new_cells.append(cell_entry)

            region["semantic_cells"] = new_cells
            # Recalculate bbox
            region["bbox"] = _recalculate_bbox(st, angle, ridx)
            # Upgrade confidence for regions with assigned cells
            if new_cells:
                region["confidence"] = "high"

        # Add to angle_anchors.ground_truth_angles
        anchors = data.setdefault("angle_anchors", {
            "ground_truth_angles": [],
            "propagated_angles": [],
        })
        gt = anchors.setdefault("ground_truth_angles", [])
        if angle not in gt:
            gt.append(angle)
            gt.sort()

    # Atomic write via tmp file + os.replace()
    anchor_path = st.anchor_path
    fd_tmp, tmp_path = tempfile.mkstemp(
        dir=str(anchor_path.parent),
        suffix=".tmp",
        prefix=anchor_path.stem,
    )
    try:
        with os.fdopen(fd_tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_path, str(anchor_path))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    st.dirty = False
    st.dirty_angles.clear()
    return f"Saved to {anchor_path.name}"


def _handle_anchor_prompt_key(st: AnchorReviewState, key: str) -> bool:
    """Handle key input during prompt modes. Returns True if key was consumed."""
    if st.prompt_mode == "new_region":
        if key == "ESCAPE":
            st.prompt_mode = ""
            st.prompt_buffer = ""
            st.status = "Cancelled"
            return True
        if key == "ENTER":
            name = st.prompt_buffer.strip()
            st.prompt_mode = ""
            st.prompt_buffer = ""
            if not name:
                st.status = "Empty name, cancelled"
                return True
            ridx = _create_new_region(st, name)
            _assign_cells_to_region(st, ridx)
            return True
        if key == "BACKSPACE":
            st.prompt_buffer = st.prompt_buffer[:-1]
            return True
        if len(key) == 1 and key.isprintable():
            st.prompt_buffer += key
            return True
        return True

    if st.prompt_mode == "bg_region":
        if key == "ESCAPE":
            # Abort both fg and bg
            st.prompt_mode = ""
            st.pending_fg_region = None
            st.status = "Half-block assignment cancelled"
            return True
        if key == "n":
            # Fall through -- apply fg only, user can create new region separately
            st.prompt_mode = ""
            if st.pending_fg_region is not None:
                _assign_cells_to_region(st, st.pending_fg_region)
            st.pending_fg_region = None
            st.status = "bg: use 'n' for new region (fg assigned)"
            return True
        if key.isdigit() and key != "0":
            bg_ridx = int(key) - 1
            regions = st.regions_at_angle()
            if bg_ridx < len(regions):
                # Apply fg assignment first
                fg_ridx = st.pending_fg_region
                if fg_ridx is not None:
                    _assign_cells_to_region(st, fg_ridx)
                    # Record dual assignments
                    angle = st.current_angle
                    for (x, y) in st.selected_cells:
                        st.dual_assignments[(angle, x, y)] = (fg_ridx, bg_ridx)
                    st.status = f"fg: {regions[fg_ridx]['name']}, bg: {regions[bg_ridx]['name']}"
                st.prompt_mode = ""
                st.pending_fg_region = None
                return True
            else:
                st.status = f"No region #{int(key)} at this angle"
                return True
        return True

    return False


def _handle_anchor_key(
    st: AnchorReviewState,
    key: str,
    cell_data: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]],
) -> bool:
    """Handle a key press in anchor review mode. Returns False to quit."""
    # Check prompt modes first
    if _handle_anchor_prompt_key(st, key):
        return True

    # --- Navigation ---
    if key == "UP":
        st.cursor_y = max(0, st.cursor_y - 1)
        st.status = ""
        return True
    if key == "DOWN":
        st.cursor_y = min(st.frame_h - 1, st.cursor_y + 1)
        st.status = ""
        return True
    if key == "LEFT":
        st.cursor_x = max(0, st.cursor_x - 1)
        st.status = ""
        return True
    if key == "RIGHT":
        st.cursor_x = min(st.frame_w - 1, st.cursor_x + 1)
        st.status = ""
        return True

    # --- Angle navigation ---
    if key in ("a", "A"):
        st.current_angle = (st.current_angle - 1) % st.num_angles
        st.selected_cells.clear()
        st.rect_start = None
        st.visited_angles.add(st.current_angle)
        st.status = f"Angle {st.current_angle}"
        st.quit_pending = False
        return True
    if key in ("d", "D"):
        st.current_angle = (st.current_angle + 1) % st.num_angles
        st.selected_cells.clear()
        st.rect_start = None
        st.visited_angles.add(st.current_angle)
        st.status = f"Angle {st.current_angle}"
        st.quit_pending = False
        return True

    # --- Frame navigation (w/s anim group, ,/. frame within anim) ---
    if key in ("w", "W"):
        st.current_anim = (st.current_anim - 1) % len(st.anim_lengths)
        st.current_frame = 0
        st.status = f"Anim {st.current_anim} frame {st.current_frame}/{st.anim_lengths[st.current_anim]}"
        st.quit_pending = False
        return True
    if key in ("s", "S"):
        st.current_anim = (st.current_anim + 1) % len(st.anim_lengths)
        st.current_frame = 0
        st.status = f"Anim {st.current_anim} frame {st.current_frame}/{st.anim_lengths[st.current_anim]}"
        st.quit_pending = False
        return True
    if key == ",":
        anim_len = st.anim_lengths[st.current_anim]
        st.current_frame = (st.current_frame - 1) % anim_len
        st.status = f"Anim {st.current_anim} frame {st.current_frame}/{anim_len}"
        st.quit_pending = False
        return True
    if key == ".":
        anim_len = st.anim_lengths[st.current_anim]
        st.current_frame = (st.current_frame + 1) % anim_len
        st.status = f"Anim {st.current_anim} frame {st.current_frame}/{anim_len}"
        st.quit_pending = False
        return True

    # --- Cell toggle (x) ---
    if key in ("x", "X"):
        pos = (st.cursor_x, st.cursor_y)
        if pos in st.selected_cells:
            st.selected_cells.discard(pos)
        else:
            st.selected_cells.add(pos)
        st.status = f"Selection: {len(st.selected_cells)} cells"
        st.quit_pending = False
        return True

    # --- Rectangle mark (m) ---
    if key in ("m", "M"):
        if st.rect_start is None:
            st.rect_start = (st.cursor_x, st.cursor_y)
            st.status = f"Rect start at ({st.cursor_x},{st.cursor_y}) - move cursor and press m again"
        else:
            x0, y0 = st.rect_start
            x1, y1 = st.cursor_x, st.cursor_y
            for ry in range(min(y0, y1), max(y0, y1) + 1):
                for rx in range(min(x0, x1), max(x0, x1) + 1):
                    st.selected_cells.add((rx, ry))
            st.rect_start = None
            st.status = f"Rect selected: {len(st.selected_cells)} cells"
        st.quit_pending = False
        return True

    # --- Clear selection (Escape) ---
    if key == "ESCAPE":
        st.selected_cells.clear()
        st.rect_start = None
        st.status = "Selection cleared"
        st.quit_pending = False
        return True

    # --- Region assignment (1-9) ---
    if key.isdigit() and key != "0":
        region_idx = int(key) - 1
        regions = st.regions_at_angle()
        if region_idx >= len(regions):
            st.status = f"No region #{int(key)} at angle {st.current_angle}"
            return True
        if not st.selected_cells:
            st.status = "No cells selected"
            return True

        # Half-block mode check
        if st.half_block_mode:
            has_half_block = False
            for (x, y) in st.selected_cells:
                if 0 <= y < len(cell_data) and 0 <= x < len(cell_data[0]):
                    glyph, fg, bg = cell_data[y][x]
                    if glyph in _HALF_BLOCK_GLYPHS and fg != bg:
                        has_half_block = True
                        break
            if has_half_block:
                # Two-step: assign fg first, then prompt for bg
                st.pending_fg_region = region_idx
                st.prompt_mode = "bg_region"
                st.status = f"fg: assigned to '{regions[region_idx]['name']}' -- now pick bg region"
                return True

        _assign_cells_to_region(st, region_idx)
        st.quit_pending = False
        return True

    # --- New region (n) ---
    if key in ("n", "N"):
        if not st.selected_cells:
            st.status = "No cells selected"
            return True
        st.prompt_mode = "new_region"
        st.prompt_buffer = ""
        st.quit_pending = False
        return True

    # --- Unassign (Backspace) ---
    if key == "BACKSPACE":
        if not st.selected_cells:
            st.status = "No cells selected"
            return True
        _unassign_cells(st)
        st.quit_pending = False
        return True

    # --- Half-block mode toggle (h) ---
    if key in ("h", "H"):
        st.half_block_mode = not st.half_block_mode
        st.status = "Half-block mode ON" if st.half_block_mode else "Half-block mode OFF"
        st.quit_pending = False
        return True

    # --- Autoplay toggle (p) ---
    if key in ("p", "P"):
        st.autoplay = not st.autoplay
        if st.autoplay:
            st.autoplay_last = time.monotonic()
            st.status = "Autoplay ON"
        else:
            st.status = "Autoplay OFF"
        st.quit_pending = False
        return True

    # --- Region focus cycling (r/f) ---
    if key in ("r", "R"):
        regions = st.regions_at_angle()
        if not regions:
            st.status = "No regions at this angle"
            return True
        if st.region_focus is None:
            st.region_focus = 0
        else:
            st.region_focus = (st.region_focus + 1) % len(regions)
        st.status = f"Focus: {regions[st.region_focus]['name']}"
        st.quit_pending = False
        return True
    if key in ("f", "F"):
        regions = st.regions_at_angle()
        if not regions:
            st.status = "No regions at this angle"
            return True
        if st.region_focus is None:
            st.region_focus = len(regions) - 1
        else:
            st.region_focus = (st.region_focus - 1) % len(regions)
        st.status = f"Focus: {regions[st.region_focus]['name']}"
        st.quit_pending = False
        return True

    # --- Save (Ctrl+S) ---
    if key == "CTRL_S":
        try:
            msg = _save_anchor(st)
            st.status = msg
        except Exception as exc:
            st.status = f"Save failed: {exc}"
        st.quit_pending = False
        return True

    # --- Quit (q) ---
    if key in ("q", "Q"):
        if st.dirty and not st.quit_pending:
            st.quit_pending = True
            st.status = "Unsaved changes! Press q again to discard, or Ctrl+S to save."
            return True
        return False

    return True


def _load_frame_cell_data_from_xp(
    st: AnchorReviewState,
    asset: RawAsset,
    layer_index: int,
) -> list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]]:
    """Load raw cell data for the current angle/anim/frame from the XP asset."""
    meta = asset.entry.meta
    # Compute atlas position for this angle + anim + frame
    angle = st.current_angle
    frame_base = sum(meta.anim_lengths[:st.current_anim]) + st.current_frame
    atlas_idx = frame_base + angle * meta.fr_num_x
    fr_x = atlas_idx % meta.fr_num_x
    fr_y = atlas_idx // meta.fr_num_x
    x0 = fr_x * meta.fr_width
    y0 = fr_y * meta.fr_height

    layer = asset.xp.layers[layer_index]
    rows: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]] = []
    for local_y in range(st.frame_h):
        row: list[tuple[int, tuple[int, int, int], tuple[int, int, int]]] = []
        for local_x in range(st.frame_w):
            sy = y0 + local_y
            sx = x0 + local_x
            if sy < len(layer.data) and sx < len(layer.data[sy]):
                glyph, fg, bg = layer.data[sy][sx]
            else:
                glyph, fg, bg = 0, (0, 0, 0), (0, 0, 0)
            row.append((glyph, tuple(fg), tuple(bg)))
        rows.append(row)
    return rows


def run_anchor_batch(anchor_path: Path, batch_ops_json: str | None, sprite_dir: Path = SPRITE_DIR) -> int:
    """Non-interactive batch mode for agent-driven anchor modifications.

    Operations format (JSON array):
      {"op": "assign", "angle": 0, "cells": [[x,y],...], "region": "arms"}
      {"op": "unassign", "angle": 0, "cells": [[x,y],...]}
      {"op": "create_region", "angle": 0, "region": "arms", "cells": [[x,y],...]}
      {"op": "dump", "angle": 0}  -- prints region summary for the angle
    """
    if not anchor_path.exists():
        print(f"anchor file not found: {anchor_path}", file=sys.stderr)
        return 1

    with open(anchor_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    if not batch_ops_json:
        # No ops = just dump all angles summary
        for angle_key in sorted(data.get("frames", {}).keys(), key=int):
            frame = data["frames"][angle_key]
            regions = frame.get("regions", [])
            print(f"Angle {angle_key}: {len(regions)} regions")
            for idx, r in enumerate(regions):
                cells = r.get("semantic_cells", [])
                print(f"  {idx+1}. {r['name']} ({len(cells)} cells) bbox={r.get('bbox')}")
        return 0

    ops = json.loads(batch_ops_json)
    modified = False

    for op in ops:
        action = op["op"]
        angle_key = str(op.get("angle", 0))
        frame = data["frames"].get(angle_key)
        if frame is None:
            print(f"Warning: angle {angle_key} not found, skipping", file=sys.stderr)
            continue
        regions = frame.get("regions", [])

        if action == "dump":
            print(f"Angle {angle_key}: {len(regions)} regions")
            for idx, r in enumerate(regions):
                cells = r.get("semantic_cells", [])
                print(f"  {idx+1}. {r['name']} ({len(cells)} cells) bbox={r.get('bbox')}")
            continue

        cells_to_process = set(tuple(c) for c in op.get("cells", []))
        target_region = op.get("region", "")

        if action == "unassign":
            for r in regions:
                before = len(r.get("semantic_cells", []))
                r["semantic_cells"] = [
                    c for c in r.get("semantic_cells", [])
                    if (c["x"], c["y"]) not in cells_to_process
                ]
                removed = before - len(r["semantic_cells"])
                if removed:
                    print(f"  Unassigned {removed} cells from '{r['name']}' at angle {angle_key}")
                    # Recalculate bbox
                    if r["semantic_cells"]:
                        xs = [c["x"] for c in r["semantic_cells"]]
                        ys = [c["y"] for c in r["semantic_cells"]]
                        r["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
                    else:
                        r["bbox"] = [0, 0, 0, 0]
                    modified = True

        elif action == "assign":
            target_r = next((r for r in regions if r["name"] == target_region), None)
            if target_r is None:
                print(f"  Region '{target_region}' not found at angle {angle_key}", file=sys.stderr)
                continue
            existing = set((c["x"], c["y"]) for c in target_r.get("semantic_cells", []))
            added = 0
            for (x, y) in cells_to_process:
                if (x, y) not in existing:
                    target_r["semantic_cells"].append({"x": x, "y": y, "glyph": 0, "fg": "", "bg": "", "role": ""})
                    added += 1
            if added:
                xs = [c["x"] for c in target_r["semantic_cells"]]
                ys = [c["y"] for c in target_r["semantic_cells"]]
                target_r["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
                print(f"  Assigned {added} cells to '{target_region}' at angle {angle_key}")
                modified = True

        elif action == "create_region":
            existing_r = next((r for r in regions if r["name"] == target_region), None)
            if existing_r is not None:
                print(f"  Region '{target_region}' already exists at angle {angle_key}, use 'assign'", file=sys.stderr)
                continue
            new_cells = [{"x": x, "y": y, "glyph": 0, "fg": "", "bg": "", "role": ""} for (x, y) in cells_to_process]
            xs = [c["x"] for c in new_cells]
            ys = [c["y"] for c in new_cells]
            regions.append({
                "name": target_region,
                "bbox": [min(xs), min(ys), max(xs), max(ys)],
                "confidence": "medium",
                "palette_roles": [],
                "semantic_cells": new_cells,
            })
            print(f"  Created region '{target_region}' with {len(new_cells)} cells at angle {angle_key}")
            modified = True

    if modified:
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode="w", dir=anchor_path.parent, suffix=".json", delete=False)
        json.dump(data, tmp, indent=2, ensure_ascii=False)
        tmp.write("\n")
        tmp.close()
        os.replace(tmp.name, anchor_path)
        print(f"Saved: {anchor_path}")

    return 0


def run_anchor_review(anchor_path: Path, sprite_dir: Path = SPRITE_DIR) -> int:
    """Run the interactive anchor review mode."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("anchor review requires a TTY", file=sys.stderr)
        return 1
    if layer2_browser.select is None or layer2_browser.termios is None or layer2_browser.tty is None:
        print("anchor review requires POSIX termios support", file=sys.stderr)
        return 1

    if not anchor_path.exists():
        print(f"anchor file not found: {anchor_path}", file=sys.stderr)
        return 1

    st = _load_anchor_state(anchor_path)

    # Locate the XP sprite from anchor data reference_xp field
    ref_xp = st.anchor_data.get("reference_xp", "")
    if ref_xp:
        ref_path = (anchor_path.parent / ref_xp).resolve()
    else:
        ref_path = None

    if ref_path is None or not ref_path.exists():
        print(f"reference XP not found: {ref_xp} (at: {ref_path})", file=sys.stderr)
        return 1

    # Load the XP asset
    entry = _resolve_sprite_entry(str(ref_path), sprite_dir)
    asset = _load_raw_asset(entry)
    semantic_layer = st.anchor_data.get("semantic_layer", 2)
    layer_index = min(semantic_layer, asset.layer_count - 1)

    # Use the asset's actual anim_lengths (from XP layer-0 metadata)
    st.anim_lengths = list(asset.entry.meta.anim_lengths)

    # Mark initial angle as visited (view-only, not dirty)
    st.visited_angles.add(st.current_angle)

    redraw_pending = [True]

    def on_resize(*_: object) -> None:
        redraw_pending[0] = True

    old_sigwinch = signal.signal(signal.SIGWINCH, on_resize)
    fd = sys.stdin.fileno()
    old_settings = layer2_browser.termios.tcgetattr(fd)

    sys.stdout.write("\033[?1049h\033[?25l")  # alt screen, hide cursor
    sys.stdout.flush()

    try:
        layer2_browser.tty.setraw(fd)
        # Disable XOFF flow control AFTER setraw so IXON clear is not overwritten
        new_settings = layer2_browser.termios.tcgetattr(fd)
        new_settings[0] = new_settings[0] & ~layer2_browser.termios.IXON  # iflag
        layer2_browser.termios.tcsetattr(fd, layer2_browser.termios.TCSADRAIN, new_settings)

        cell_data = _load_frame_cell_data_from_xp(st, asset, layer_index)
        prev_angle = st.current_angle
        prev_anim = st.current_anim
        prev_frame = st.current_frame

        while True:
            if st.current_angle != prev_angle or st.current_anim != prev_anim or st.current_frame != prev_frame:
                cell_data = _load_frame_cell_data_from_xp(st, asset, layer_index)
                prev_angle = st.current_angle
                prev_anim = st.current_anim
                prev_frame = st.current_frame

            # Autoplay: advance angle on timer
            if st.autoplay:
                now = time.monotonic()
                if now - st.autoplay_last >= st.autoplay_interval:
                    st.current_angle = (st.current_angle + 1) % st.num_angles
                    st.autoplay_last = now
                    st.visited_angles.add(st.current_angle)
                    redraw_pending[0] = True

            if redraw_pending[0] or st.autoplay:
                redraw_pending[0] = False
                sys.stdout.write(_anchor_compose_screen(st, cell_data, asset=asset, layer_index=layer_index))
                sys.stdout.flush()

            key = _read_anchor_key(fd)
            if key is None:
                continue

            keep_running = _handle_anchor_key(st, key, cell_data)
            if not keep_running:
                break
            redraw_pending[0] = True

    finally:
        layer2_browser.termios.tcsetattr(fd, layer2_browser.termios.TCSADRAIN, old_settings)
        sys.stdout.write("\033[?1049l\033[?25h\033[0m")  # restore screen, show cursor
        sys.stdout.flush()
        signal.signal(signal.SIGWINCH, old_sigwinch)

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sprite-dir", type=Path, default=SPRITE_DIR)
    parser.add_argument("--sprite", help="Sprite filename or path for exact non-interactive dump mode.")
    parser.add_argument("--layer", type=int, help="Exact layer index for non-interactive dump mode.")
    parser.add_argument("--anim", type=int, default=0, help="0-based animation group index for dump mode.")
    parser.add_argument("--frame", type=int, default=0, help="0-based frame index within the selected animation group.")
    parser.add_argument("--angle", type=int, default=0, help="0-based angle index for dump mode.")
    parser.add_argument("--row-lo", type=int, help="Optional 0-based frame-relative row lower bound.")
    parser.add_argument("--row-hi", type=int, help="Optional 0-based frame-relative row upper bound.")
    parser.add_argument("--col-lo", type=int, help="Optional 0-based frame-relative column lower bound.")
    parser.add_argument("--col-hi", type=int, help="Optional 0-based frame-relative column upper bound.")
    parser.add_argument("--json", action="store_true", help="Emit exact JSON dump instead of launching the TTY browser.")
    parser.add_argument("--out", type=Path, help="Optional JSON output file for dump mode.")
    parser.add_argument(
        "--anchor-review",
        type=Path,
        metavar="PATH",
        help="Launch interactive anchor review mode with the given anchor JSON file.",
    )
    parser.add_argument(
        "--anchor-batch",
        type=Path,
        metavar="PATH",
        help="Non-interactive batch mode: apply operations to anchor JSON without TTY.",
    )
    parser.add_argument(
        "--batch-ops",
        type=str,
        help=(
            "Batch operations as JSON array. Each op: "
            '{"op":"unassign"|"assign"|"create_region","angle":N,"cells":[[x,y],...],"region":"name"}'
        ),
    )
    args = parser.parse_args(argv)

    # Anchor batch mode (agent-friendly, no TTY required)
    if args.anchor_batch is not None:
        return run_anchor_batch(args.anchor_batch, args.batch_ops, sprite_dir=args.sprite_dir)

    # Anchor review mode takes priority
    if args.anchor_review is not None:
        return run_anchor_review(args.anchor_review, sprite_dir=args.sprite_dir)

    dump_mode = args.json or args.sprite is not None or args.layer is not None
    if dump_mode:
        if not args.sprite:
            raise SystemExit("dump mode requires --sprite")
        if args.layer is None:
            raise SystemExit("dump mode requires --layer")
        try:
            return run_exact_dump(
                sprite_dir=args.sprite_dir,
                sprite=args.sprite,
                layer_index=args.layer,
                anim_index=args.anim,
                frame_idx=args.frame,
                angle=args.angle,
                row_lo=args.row_lo,
                row_hi=args.row_hi,
                col_lo=args.col_lo,
                col_hi=args.col_hi,
                out_path=args.out,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
    return run_raw_layer_browser(args.sprite_dir)


if __name__ == "__main__":
    raise SystemExit(main())
