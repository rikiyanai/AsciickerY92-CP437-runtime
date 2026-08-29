#!/usr/bin/env python3
"""Launcher TUI browser for merged layer-2-plus sprite previews.

This browser is intentionally not a raw-layer inspector. It shows the legacy
launcher preview surface: layer 2 with authored overlay layers merged on top.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import select
    import termios
    import tty
except ImportError:
    select = None
    termios = None
    tty = None

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.xp_core import XPFile
from scripts.cli_style import sparkline

SPRITE_DIR = REPO_ROOT / "assets" / "sprites"
TRANSPARENT_RGB = (255, 0, 255)
SWOOSH_RGB = (0, 255, 255)
TRANSPARENT_INDEX = 255
FULL_BLOCK = 219
HALF_LOWER = 220
HALF_LEFT = 221
HALF_RIGHT = 222
HALF_UPPER = 223
MASK_LOWER = 0x3
MASK_LEFT = 0x5
MASK_RIGHT = 0xA
MASK_UPPER = 0xC
MASK_FULL = 0xF
LIGHTEN_AMOUNT = 51
KEY_ESCAPE = "\x1b"
KEY_SPACE = " "
KEY_ARROW_LEFT = "LEFT"
KEY_ARROW_RIGHT = "RIGHT"
KEY_ARROW_UP = "UP"
KEY_ARROW_DOWN = "DOWN"

# Ported directly from engine/sprite.cpp glyph_coverage[256].
GLYPH_COVERAGE = (
    0x0000, 0x2222, 0x4433, 0x3412, 0x2312, 0x2323, 0x2312, 0x1111,
    0x3333, 0x1111, 0x3333, 0x4122, 0x2222, 0x2203, 0x3322, 0x3322,
    0x1212, 0x2121, 0x2222, 0x2211, 0x3321, 0x2222, 0x0022, 0x2233,
    0x2211, 0x1122, 0x2121, 0x1212, 0x0111, 0x2222, 0x1122, 0x2211,
    0x0000, 0x2211, 0x1100, 0x2322, 0x2211, 0x1112, 0x2222, 0x1100,
    0x0201, 0x1201, 0x2211, 0x1111, 0x0011, 0x1100, 0x0011, 0x2102,
    0x3222, 0x1211, 0x2112, 0x2121, 0x2221, 0x2221, 0x1222, 0x2101,
    0x2222, 0x2211, 0x1111, 0x1111, 0x1111, 0x1111, 0x1101, 0x2111,
    0x3212, 0x2222, 0x2322, 0x1212, 0x2322, 0x2312, 0x2302, 0x1222,
    0x2222, 0x1111, 0x2012, 0x2322, 0x0322, 0x3322, 0x2322, 0x2212,
    0x2302, 0x2221, 0x2312, 0x2221, 0x2211, 0x2222, 0x2211, 0x2222,
    0x2222, 0x2211, 0x2322, 0x1212, 0x0320, 0x2121, 0x1200, 0x0011,
    0x1100, 0x1122, 0x1322, 0x1112, 0x2122, 0x1112, 0x1202, 0x1122,
    0x1322,
    0x1111, 0x2121, 0x1212, 0x1111, 0x1222, 0x1122, 0x1112, 0x1113,
    0x1122, 0x1112, 0x1121, 0x1211, 0x1122, 0x1111, 0x1122, 0x1112,
    0x1122, 0x1112, 0x1211, 0x1111, 0x1111, 0x1200, 0x1122, 0x1212,
    0x1122, 0x2112, 0x2222, 0x1122, 0x1222, 0x2222, 0x1212, 0x2212,
    0x2212, 0x1212, 0x1111, 0x2211, 0x1211, 0x2222, 0x1122, 0x1212,
    0x1122, 0x3322, 0x2222, 0x1122, 0x1122, 0x1222, 0x1222, 0x1122,
    0x2222, 0x2222, 0x1212, 0x1312, 0x2222, 0x1221, 0x3112, 0x1222,
    0x1111, 0x2122, 0x1122, 0x1322, 0x2222, 0x2211, 0x2211, 0x1112,
    0x1101, 0x1110, 0x2232, 0x2232, 0x1122, 0x2211, 0x2211, 0x1111,
    0x2222, 0x3333, 0x1111, 0x1212, 0x1313, 0x2222, 0x1123, 0x1213,
    0x2222, 0x2222, 0x2222, 0x2222, 0x2311, 0x1312, 0x0112, 0x2110,
    0x2211, 0x1122, 0x2121, 0x1111, 0x2222, 0x3131, 0x2222, 0x2222,
    0x2222, 0x2222, 0x2222, 0x2222, 0x2222, 0x2222, 0x2222, 0x3311,
    0x2222, 0x0033, 0x3211, 0x3121, 0x2131, 0x1132, 0x3333, 0x3333,
    0x1201, 0x1021, 0x4444, 0x0044, 0x0404, 0x4040, 0x4400, 0x1212,
    0x2212, 0x1201, 0x2222, 0x1212, 0x1112, 0x1112, 0x1211, 0x2222,
    0x2212, 0x2222, 0x1222, 0x1212, 0x2213, 0x1211, 0x2222, 0x2211,
    0x1312, 0x0212, 0x0211, 0x1202, 0x2012, 0x1111, 0x1212, 0x2200,
    0x0000, 0x0000, 0x2011, 0x2200, 0x2100, 0x2222, 0x1111,
)


@dataclass(frozen=True)
class SpriteMetadata:
    angles: int
    projs: int
    anim_lengths: tuple[int, ...]
    anim_sum: int
    fr_num_x: int
    fr_num_y: int
    fr_width: int
    fr_height: int


@dataclass(frozen=True)
class SpriteEntry:
    path: Path
    name: str
    meta: SpriteMetadata

    @property
    def display_path(self) -> str:
        try:
            return str(self.path.relative_to(REPO_ROOT))
        except ValueError:
            return str(self.path)


@dataclass(frozen=True)
class SpriteAsset:
    entry: SpriteEntry
    color_key: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]]
    merged_visual: list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]]


@dataclass
class BrowserState:
    sprite_index: int = 0
    anim: int = 0
    frame: int = 0
    yaw: float = 0.0
    timing: tuple[int, int, int, int] = (20, 2, 10, 4)
    autoplay: bool = True
    status: str = ""


@dataclass(frozen=True)
class PreviewCell:
    glyph: int
    fg: tuple[int, int, int] | None
    bg: tuple[int, int, int] | None


def _load_xp_quiet(path: Path) -> XPFile:
    with contextlib.redirect_stdout(io.StringIO()):
        return XPFile(str(path))


def _get_digit(glyph: int) -> int:
    if 48 <= glyph <= 57:
        return glyph - 48
    if 65 <= glyph <= 90:
        return glyph + 10 - 65
    if 97 <= glyph <= 122:
        return glyph + 10 - 97
    return -1


def _parse_metadata(xp: XPFile) -> SpriteMetadata | None:
    if len(xp.layers) < 3:
        return None
    layer0 = xp.layers[0]
    visual = xp.layers[2]

    raw_angles = _get_digit(layer0.data[0][0][0])
    if raw_angles > 0:
        angles = raw_angles
        projs = 2
    else:
        angles = 1
        projs = 1

    anim_lengths: list[int] = []
    anim_sum = 0
    for x in range(1, layer0.width):
        length = _get_digit(layer0.data[0][x][0])
        if length <= 0:
            break
        anim_lengths.append(length)
        anim_sum += length

    if not anim_lengths:
        anim_lengths = [1]
        anim_sum = 1

    fr_num_x = projs * anim_sum
    fr_num_y = angles
    if fr_num_x <= 0 or fr_num_y <= 0:
        return None
    if visual.width % fr_num_x != 0 or visual.height % fr_num_y != 0:
        return None

    return SpriteMetadata(
        angles=angles,
        projs=projs,
        anim_lengths=tuple(anim_lengths),
        anim_sum=anim_sum,
        fr_num_x=fr_num_x,
        fr_num_y=fr_num_y,
        fr_width=visual.width // fr_num_x,
        fr_height=visual.height // fr_num_y,
    )


def scan_sprite_entries(sprite_dir: Path = SPRITE_DIR) -> list[SpriteEntry]:
    entries: list[SpriteEntry] = []
    for path in sorted(sprite_dir.glob("*.xp"), key=lambda item: item.name.lower()):
        try:
            xp = _load_xp_quiet(path)
            meta = _parse_metadata(xp)
        except Exception:
            meta = None
        if meta is None:
            continue
        entries.append(SpriteEntry(path=path, name=path.name, meta=meta))
    return entries


def _rgb_to_pal(rgb: tuple[int, int, int], rgb_div: int = 255) -> int:
    r = min(5, max(0, (rgb[0] * 5 + 128) // rgb_div))
    g = min(5, max(0, (rgb[1] * 5 + 128) // rgb_div))
    b = min(5, max(0, (rgb[2] * 5 + 128) // rgb_div))
    return 16 + 36 * r + 6 * g + b


def _pal_to_rgb(pal: int) -> tuple[int, int, int]:
    pal -= 16
    r = pal // 36
    pal -= r * 36
    g = pal // 6
    b = pal - g * 6
    return (r * 51, g * 51, b * 51)


def _lighten_color(pal: int) -> int:
    pal -= 16
    r = pal // 36
    pal -= 36 * r
    g = pal // 6
    b = pal - 6 * g
    r = min(5, r + 1)
    g = min(5, g + 1)
    b = min(5, b + 1)
    return 16 + 36 * r + 6 * g + b


def _average_glyph_transp(glyph: int, fg: int, bg: int, mask: int) -> int:
    coverage = GLYPH_COVERAGE[glyph & 0xFF]
    num = 0
    total = 0
    if mask & 1:
        total += coverage & 0xF
        num += 1
    if mask & 2:
        total += (coverage >> 4) & 0xF
        num += 1
    if mask & 4:
        total += (coverage >> 8) & 0xF
        num += 1
    if mask & 8:
        total += (coverage >> 12) & 0xF
        num += 1
    if total > num * 2:
        return fg
    return bg


def _lighten_rgb(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(min(255, value + LIGHTEN_AMOUNT) for value in rgb)


def _mask_for_half_block(glyph: int) -> int:
    if glyph == HALF_LOWER:
        return MASK_LOWER
    if glyph == HALF_LEFT:
        return MASK_LEFT
    if glyph == HALF_RIGHT:
        return MASK_RIGHT
    if glyph == HALF_UPPER:
        return MASK_UPPER
    return 0


def _merge_layers(xp: XPFile) -> list[list[tuple[int, tuple[int, int, int], tuple[int, int, int]]]]:
    layer0 = xp.layers[0]
    merged = [
        [tuple(cell) for cell in row]
        for row in xp.layers[2].data
    ]

    for layer_index in range(3, len(xp.layers)):
        merge_layer = xp.layers[layer_index]
        is_last = layer_index == len(xp.layers) - 1
        for y in range(merge_layer.height):
            for x in range(merge_layer.width):
                merge_glyph, merge_fg, merge_bg = merge_layer.data[y][x]
                key_rgb = layer0.data[y][x][2]
                current_glyph, current_fg, current_bg = merged[y][x]

                if is_last and merge_fg == SWOOSH_RGB:
                    fg_transp = current_fg == key_rgb
                    bk_transp = current_bg == key_rgb
                    if current_bg == TRANSPARENT_RGB:
                        fg_transp = True
                        bk_transp = True

                    swoosh_bk_transp = merge_bg == key_rgb
                    if merge_bg == SWOOSH_RGB:
                        merge_glyph = FULL_BLOCK

                    mask = _mask_for_half_block(merge_glyph)
                    if merge_glyph in (0, 32):
                        if merge_bg != TRANSPARENT_RGB:
                            merged[y][x] = (merge_glyph, merge_fg, merge_bg)
                        continue

                    if mask:
                        ansi_fg = TRANSPARENT_INDEX if fg_transp else _rgb_to_pal(current_fg)
                        ansi_bg = TRANSPARENT_INDEX if bk_transp else _rgb_to_pal(current_bg)
                        if swoosh_bk_transp:
                            avg_fg = _average_glyph_transp(current_glyph, ansi_fg, ansi_bg, mask)
                            avg_bg = _average_glyph_transp(current_glyph, ansi_fg, ansi_bg, MASK_FULL ^ mask)
                            if avg_fg == TRANSPARENT_INDEX:
                                next_fg = SWOOSH_RGB
                            else:
                                next_fg = _pal_to_rgb(_lighten_color(avg_fg))
                            if avg_fg == TRANSPARENT_INDEX:
                                next_bg = key_rgb
                            else:
                                next_bg = _pal_to_rgb(avg_bg)
                            merged[y][x] = (merge_glyph, next_fg, next_bg)
                        else:
                            avg_fg = _average_glyph_transp(current_glyph, ansi_fg, ansi_bg, mask)
                            if avg_fg == TRANSPARENT_INDEX:
                                next_fg = SWOOSH_RGB
                            else:
                                next_fg = _pal_to_rgb(_lighten_color(avg_fg))
                            merged[y][x] = (merge_glyph, next_fg, merge_bg)
                        continue

                    if fg_transp and bk_transp:
                        merged[y][x] = (merge_glyph, merge_fg, merge_bg)
                    else:
                        next_fg = SWOOSH_RGB if fg_transp else _lighten_rgb(current_fg)
                        next_bg = SWOOSH_RGB if bk_transp else _lighten_rgb(current_bg)
                        merged[y][x] = (current_glyph, next_fg, next_bg)
                    continue

                if merge_bg != TRANSPARENT_RGB:
                    merged[y][x] = (merge_glyph, merge_fg, merge_bg)

    return merged


def load_sprite_asset(entry: SpriteEntry) -> SpriteAsset:
    xp = _load_xp_quiet(entry.path)
    return SpriteAsset(
        entry=entry,
        color_key=xp.layers[0].data,
        merged_visual=_merge_layers(xp),
    )


def default_browser_state(meta: SpriteMetadata) -> BrowserState:
    anim = 1 if len(meta.anim_lengths) > 1 else 0
    if anim:
        timing = (0, 4, 0, 0)
    else:
        timing = (20, 2, 10, 4)
    return BrowserState(anim=anim, timing=timing, autoplay=True)


def _quantize_preview_rgb(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    return _pal_to_rgb(_rgb_to_pal(rgb))


def _normalize_preview_cell(
    glyph: int,
    fg: tuple[int, int, int] | None,
    bg: tuple[int, int, int] | None,
) -> PreviewCell:
    if fg is None and bg is None:
        return PreviewCell(32, None, None)
    if fg is None:
        if glyph == FULL_BLOCK:
            return PreviewCell(32, None, bg)
        if glyph == HALF_LOWER:
            return PreviewCell(HALF_UPPER, bg, None)
        if glyph == HALF_UPPER:
            return PreviewCell(HALF_LOWER, bg, None)
        if glyph == HALF_LEFT:
            return PreviewCell(HALF_RIGHT, bg, None)
        if glyph == HALF_RIGHT:
            return PreviewCell(HALF_LEFT, bg, None)
        return PreviewCell(32, None, bg)
    return PreviewCell(glyph if glyph else 32, fg, bg)


def _select_frame(meta: SpriteMetadata, state: BrowserState, time_tick: int) -> tuple[int, int, int]:
    anim = state.anim
    if anim < 0 or anim >= len(meta.anim_lengths):
        anim = 0
    anim_length = meta.anim_lengths[anim]

    if state.autoplay:
        t0, t1, t2, t3 = state.timing
    else:
        t0 = t1 = t2 = t3 = 0
    total_len = t0 + t1 * anim_length + t2 + t3 * anim_length

    if total_len <= 0:
        frame = state.frame % anim_length
    else:
        time_pos = time_tick % total_len
        if time_pos < t0:
            frame = 0
        elif time_pos < t0 + t1 * anim_length:
            frame = (time_pos - t0) // t1
        elif time_pos < t0 + t1 * anim_length + t2:
            frame = anim_length - 1
        else:
            frame = anim_length - 1 - (time_pos - t0 - t1 * anim_length - t2) // t3

    angle = int(((state.yaw) * meta.angles / 360.0) + 0.5)
    angle %= meta.angles

    frame_base = sum(meta.anim_lengths[:anim])
    x = frame_base + frame
    y = angle
    atlas_idx = x + y * meta.fr_num_x
    return atlas_idx, angle, frame


def build_preview_cells(asset: SpriteAsset, state: BrowserState, time_tick: int) -> list[list[PreviewCell]]:
    meta = asset.entry.meta
    atlas_idx, _, _ = _select_frame(meta, state, time_tick)
    fr_x = atlas_idx % meta.fr_num_x
    fr_y = atlas_idx // meta.fr_num_x
    x0 = fr_x * meta.fr_width
    y0 = fr_y * meta.fr_height

    preview = [[PreviewCell(32, None, None) for _ in range(16)] for _ in range(16)]
    copy_w = min(meta.fr_width, 16)
    copy_h = min(meta.fr_height, 16)
    dst_x = max(0, (16 - meta.fr_width) // 2)
    dst_y = max(0, (16 - meta.fr_height) // 2)
    src_x = max(0, (meta.fr_width - 16) // 2)
    src_y = max(0, (meta.fr_height - 16) // 2)

    for y in range(copy_h):
        for x in range(copy_w):
            src_gx = x0 + src_x + x
            src_gy = y0 + src_y + y
            glyph, fg_rgb, bg_rgb = asset.merged_visual[src_gy][src_gx]
            key_rgb = asset.color_key[src_gy][src_gx][2]

            fg: tuple[int, int, int] | None
            bg: tuple[int, int, int] | None
            if fg_rgb == SWOOSH_RGB:
                fg = SWOOSH_RGB
            elif bg_rgb == TRANSPARENT_RGB or fg_rgb == key_rgb:
                fg = None
            else:
                fg = _quantize_preview_rgb(fg_rgb)

            if bg_rgb == SWOOSH_RGB:
                bg = SWOOSH_RGB
            elif bg_rgb == TRANSPARENT_RGB or bg_rgb == key_rgb:
                bg = None
            else:
                bg = _quantize_preview_rgb(bg_rgb)

            preview[y + dst_y][x + dst_x] = _normalize_preview_cell(glyph, fg, bg)

    return preview


def _cp437_char(glyph: int) -> str:
    if glyph in (0, 32):
        return " "
    try:
        return bytes([glyph & 0xFF]).decode("cp437")
    except Exception:
        return "?"


def _style_cell(cell: PreviewCell) -> str:
    parts: list[str] = []
    if cell.fg is not None:
        parts.append(f"\033[38;2;{cell.fg[0]};{cell.fg[1]};{cell.fg[2]}m")
    else:
        parts.append("\033[39m")
    if cell.bg is not None:
        parts.append(f"\033[48;2;{cell.bg[0]};{cell.bg[1]};{cell.bg[2]}m")
    else:
        parts.append("\033[49m")
    parts.append(_cp437_char(cell.glyph))
    parts.append("\033[0m")
    return "".join(parts)


def _render_preview_lines(preview: list[list[PreviewCell]]) -> list[str]:
    return ["".join(_style_cell(cell) for cell in row) for row in preview]


def _time_tick() -> int:
    return (time.monotonic_ns() // 1000) >> 14


def _read_key(fd: int) -> str | None:
    if select is None:
        return None
    if not select.select([fd], [], [], 0.05)[0]:
        return None
    raw = os.read(fd, 16)
    if not raw:
        return None
    data = raw.decode("utf-8", errors="ignore")
    if data.startswith("\x1b[D"):
        return KEY_ARROW_LEFT
    if data.startswith("\x1b[C"):
        return KEY_ARROW_RIGHT
    if data.startswith("\x1b[A"):
        return KEY_ARROW_UP
    if data.startswith("\x1b[B"):
        return KEY_ARROW_DOWN
    return data[0]


def _asset_list_lines(entries: list[SpriteEntry], index: int, max_lines: int) -> list[str]:
    if max_lines <= 0:
        return []
    start = max(0, index - max_lines // 2)
    end = min(len(entries), start + max_lines)
    start = max(0, end - max_lines)

    lines = [f"Assets {start + 1}-{end} of {len(entries)}"]
    for i in range(start, end):
        prefix = ">" if i == index else " "
        lines.append(f"{prefix} {i + 1:03d} {entries[i].name}  {entries[i].display_path}")
    return lines[:max_lines]


def _frame_summary(meta: SpriteMetadata, state: BrowserState, tick: int) -> str:
    _, angle, frame = _select_frame(meta, state, tick)
    anim = min(max(state.anim, 0), len(meta.anim_lengths) - 1)
    anim_length = meta.anim_lengths[anim]
    return (
        f"anim {anim + 1}/{len(meta.anim_lengths)}  "
        f"frame {frame + 1}/{anim_length}  "
        f"angle {angle + 1}/{meta.angles}  "
        f"yaw {int(state.yaw) % 360}  "
        f"merged layer-2+ preview  crop 16x16 from {meta.fr_width}x{meta.fr_height}"
    )


def _render_screen(entries: list[SpriteEntry], index: int, asset: SpriteAsset, state: BrowserState) -> str:
    _, rows = shutil.get_terminal_size(fallback=(80, 24))
    tick = _time_tick()
    preview = _render_preview_lines(build_preview_cells(asset, state, tick))
    status = state.status or " "
    selected_path = asset.entry.display_path
    header = [
        "Layer-2-only XP browser",
        f"{index + 1}/{len(entries)}  {asset.entry.name}",
        selected_path,
        _frame_summary(asset.entry.meta, state, tick),
        "[q] quit  [←/→] sprite  [a/d] angle  [w/s] anim  [,/.] frame  [space] autoplay  [j/k] +/-10",
        "",
    ]
    asset_list_rows = max(0, rows - len(header) - len(preview) - 3)
    assets = _asset_list_lines(entries, index, asset_list_rows)
    body = header + preview + [""] + assets + ["", status]
    visible = body[: max(1, rows)]
    return "\033[H\033[2J" + "\r\n".join(visible)


def _render_loading_screen(
    current: int,
    total: int,
    *,
    current_name: str = "",
    accepted: int = 0,
) -> str:
    width = 28
    progress = current / max(total, 1)
    filled = max(1, int(round(progress * width)))
    values = [0.0] * max(0, width - filled) + [progress] * filled
    chart = sparkline(values, lo=0.0, hi=1.0)
    lines = [
        "Layer-2-only XP browser",
        "",
        "Loading merged layer-2+ asset browser...",
        chart,
        f"scanned {current}/{total}  valid {accepted}",
    ]
    if current_name:
        lines.append(f"last: {current_name}")
    return "\033[H\033[2J" + "\r\n".join(lines)


def _scan_sprite_entries_with_loading(sprite_dir: Path) -> list[SpriteEntry]:
    paths = sorted(sprite_dir.glob("*.xp"), key=lambda item: item.name.lower())
    if not paths:
        return []

    entries: list[SpriteEntry] = []
    last_draw = 0.0
    sys.stdout.write(_render_loading_screen(0, len(paths)))
    sys.stdout.flush()
    for idx, path in enumerate(paths, start=1):
        try:
            xp = _load_xp_quiet(path)
            meta = _parse_metadata(xp)
        except Exception:
            meta = None
        if meta is not None:
            entries.append(SpriteEntry(path=path, name=path.name, meta=meta))
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


def _adjust_index(index: int, delta: int, total: int) -> int:
    if total <= 0:
        return 0
    return (index + delta) % total


def _apply_key(state: BrowserState, meta: SpriteMetadata, key: str) -> tuple[bool, int | None]:
    if key in {"q", "Q", KEY_ESCAPE, "\x03"}:
        return False, None
    if key == KEY_ARROW_LEFT:
        return True, -1
    if key == KEY_ARROW_RIGHT:
        return True, 1
    if key in {"j", "J"}:
        return True, -10
    if key in {"k", "K"}:
        return True, 10
    if key in {"a", "A", KEY_ARROW_UP}:
        step = 360.0 / max(1, meta.angles)
        state.yaw = (state.yaw - step) % 360.0
        state.status = f"angle step: yaw {int(state.yaw) % 360}"
        return True, None
    if key in {"d", "D", KEY_ARROW_DOWN}:
        step = 360.0 / max(1, meta.angles)
        state.yaw = (state.yaw + step) % 360.0
        state.status = f"angle step: yaw {int(state.yaw) % 360}"
        return True, None
    if key in {"w", "W"}:
        state.anim = (state.anim - 1) % len(meta.anim_lengths)
        state.frame = 0
        state.status = f"anim {state.anim + 1}"
        return True, None
    if key in {"s", "S"}:
        state.anim = (state.anim + 1) % len(meta.anim_lengths)
        state.frame = 0
        state.status = f"anim {state.anim + 1}"
        return True, None
    if key == ",":
        state.autoplay = False
        state.frame = (state.frame - 1) % meta.anim_lengths[state.anim]
        state.status = f"frame {state.frame + 1}"
        return True, None
    if key == ".":
        state.autoplay = False
        state.frame = (state.frame + 1) % meta.anim_lengths[state.anim]
        state.status = f"frame {state.frame + 1}"
        return True, None
    if key == KEY_SPACE:
        state.autoplay = not state.autoplay
        state.status = "autoplay on" if state.autoplay else "autoplay off"
        return True, None
    return True, None


def run_sprite_browser(sprite_dir: Path = SPRITE_DIR) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("layer-2-only XP browser requires a TTY", file=sys.stderr)
        return 1
    if select is None or termios is None or tty is None:
        print("layer-2-only XP browser requires POSIX termios support", file=sys.stderr)
        return 1

    cache: dict[Path, SpriteAsset] = {}
    redraw_pending = [True]
    load_error: str | None = None
    index = 0
    asset: SpriteAsset | None = None
    state: BrowserState | None = None

    def on_resize(*_: object) -> None:
        redraw_pending[0] = True

    old_sigwinch = signal.signal(signal.SIGWINCH, on_resize)
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()

    try:
        entries = _scan_sprite_entries_with_loading(sprite_dir)
        if not entries:
            load_error = "no valid .xp sprites found"
            return 1

        def get_asset(index_value: int) -> SpriteAsset:
            entry = entries[index_value]
            cached = cache.get(entry.path)
            if cached is None:
                cached = load_sprite_asset(entry)
                cache[entry.path] = cached
            return cached

        asset = get_asset(index)
        state = default_browser_state(asset.entry.meta)
        tty.setraw(fd)
        while True:
            assert asset is not None
            assert state is not None
            if redraw_pending[0] or state.autoplay:
                redraw_pending[0] = False
                sys.stdout.write(_render_screen(entries, index, asset, state))
                sys.stdout.flush()

            key = _read_key(fd)
            if key is None:
                continue

            keep_running, index_delta = _apply_key(state, asset.entry.meta, key)
            if not keep_running:
                break
            if index_delta is not None:
                index = _adjust_index(index, index_delta, len(entries))
                asset = get_asset(index)
                state = default_browser_state(asset.entry.meta)
                state.status = asset.entry.display_path
            redraw_pending[0] = True
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\033[?1049l\033[?25h\033[0m")
        sys.stdout.flush()
        signal.signal(signal.SIGWINCH, old_sigwinch)
    if load_error:
        print(load_error, file=sys.stderr)
        return 1

    return 0


def main() -> int:
    return run_sprite_browser()


if __name__ == "__main__":
    raise SystemExit(main())
