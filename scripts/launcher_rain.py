"""launcher_rain.py — Rain animation engine for the asciicker launcher.

Deep, self-contained animation cluster (~640 lines extracted from launcher.py).
Owns all rain state (_submenu_rain dict, _RAIN_* constants, physics, rendering)
and exposes a clean interface:

- ``RainEngine(renderer, menu_items)`` — constructor
- ``rain_ui_enabled() -> bool``
- ``rain_root_choice(bar, valid_keys) -> str | None``
- ``ensure_submenu_rain(floor_row, cols)``
- ``tick_submenu_rain(floor_row, cols)``
- ``render_submenu_rain(floor_row, cols, rule_line)``
- ``run_scanline_reveal(floor_row, cols)``
- ``FRAME_S`` — frame timing constant

All physics and rendering internals are private methods.
"""

from __future__ import annotations

import math
import os
import random
import re
import select
import shutil
import sys
import termios
import time
import tty
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts.launcher_ui import Renderer

from scripts.launcher_lib import health as _health
from scripts.launcher_lib.banner import _build_banner_str, _banner_target_cols
from scripts.launcher_ui import _visible_len as _ansi_len

try:
    from _common import (
        load_banner_pixels as _rain_load_banner_pixels,
        render_banner as _rain_render_banner,
    )
except Exception:
    _rain_load_banner_pixels = None
    _rain_render_banner = None


# ── ANSI constants (only used within rain module) ─────────────────────────────

_ANSI_RESET = "\033[0m"
_ANSI_DIM = "\033[2m"
_ANSI_BOLD = "\033[1m"
_ANSI_RED = "\033[31m"
_ANSI_GREEN = "\033[32m"
_ANSI_YELLOW = "\033[33m"
_ANSI_CYAN = "\033[36m"


# ── Rain engine constants ─────────────────────────────────────────────────────

FRAME_S = 0.040
_MAX_DROPS = 60
_MAX_FRAGS = 100
_GRAVITY = 0.08
_VX = 0.22
_VY = (0.35, 1.80)
_BOUNCE_DAMP = 0.32
_FRAG_COUNT = 3
_FRAG_LIFE = 14
_SUBMENU_DROPS = 25
_BOLT_COOLDOWN_MIN = 120
_BOLT_COOLDOWN_MAX = 300
_BOLT_FRAMES = 4


# ── RainEngine class ──────────────────────────────────────────────────────────

class RainEngine:
    """Owns all rain animation state, physics, and rendering.

    ``_submenu_rain`` dict is an instance attribute, not a module global.
    The ``renderer`` dependency is injected at construction.
    """

    def __init__(self, renderer: Renderer, menu_items: list) -> None:
        self._renderer = renderer
        self._menu_items = menu_items
        self._submenu_rain: dict = {
            "drops": [], "frags": [], "splashes": [],
            "normals": {}, "banner_rows": 0, "banner_col": 0,
            "banner_cols": 0, "banner_row": 0, "layout_key": None,
            "bolt": None, "bolt_frames": 0, "bolt_cooldown": 60,
            "scanline_pending": False, "scanline_row": -1,
        }

    # ── Public interface for launcher.py ──────────────────────────────────

    @property
    def submenu_rain(self) -> dict:
        return self._submenu_rain

    @submenu_rain.setter
    def submenu_rain(self, value: dict) -> None:
        self._submenu_rain = value

    def rain_ui_enabled(self) -> bool:
        if os.name == "nt":
            return False
        return (
            sys.stdin.isatty()
            and sys.stdout.isatty()
            and not os.environ.get("CI")
            and os.environ.get("ASCIICKER_LAUNCHER_RAIN", "1") != "0"
            and os.environ.get("ASCIICKER_LAUNCHER_PLAIN") != "1"
        )

    def rain_root_choice(self, bar: _health.StatusBar, valid_keys: set[str]) -> str | None:
        """Main menu rain loop. Returns chosen key or None if rain is disabled."""
        if not self.rain_ui_enabled():
            return None
        if os.name == "nt":
            return None

        renderer_active = self._renderer.active
        fd = sys.stdin.fileno()
        old_tty = None
        if not renderer_active:
            old_tty = termios.tcgetattr(fd)
            sys.stdout.write("\033[?1049h\033[?25l")
            sys.stdout.flush()

        drops: list[list[float]] = []
        frags: list[list[float]] = []
        splashes: list[int] = []
        banner_pixels = None
        banner_src_w = 0
        banner_src_h = 0
        if _rain_load_banner_pixels is not None:
            try:
                banner_pixels, banner_src_w, banner_src_h = _rain_load_banner_pixels()
            except Exception:
                banner_pixels = None
        layout_key: tuple[int, int, int, int, int] | None = None
        banner_lines: list[str] = []
        banner_rows = 0
        banner_cols = 0
        banner_row = 0
        banner_col = 0
        normals: dict[tuple[int, int], tuple[float, float]] = {}
        try:
            if not renderer_active:
                tty.setraw(fd)
            first = True
            last_probe = 0.0
            while True:
                now = time.time()
                if now - last_probe > 8.0:
                    bar = _health.fast_probes()
                    last_probe = now

                cols, rows = shutil.get_terminal_size(fallback=(80, 24))
                rows = max(rows, 18)
                next_banner_cols = max(30, min(cols * 55 // 100, 60))
                next_banner_row = max(1, rows // 7)
                next_banner_col = max(0, (cols - next_banner_cols) // 2)
                next_layout_key = (rows, cols, next_banner_cols, next_banner_row, next_banner_col)
                if layout_key != next_layout_key:
                    layout_key = next_layout_key
                    banner_cols = next_banner_cols
                    banner_row = next_banner_row
                    banner_col = next_banner_col
                    normals = {}
                    if banner_pixels is not None and _rain_render_banner is not None:
                        try:
                            banner, banner_rows = _rain_render_banner(
                                banner_pixels, banner_src_w, banner_src_h, banner_cols,
                            )
                            banner_lines = banner.splitlines() if banner else []
                            normals, banner_rows = self._build_banner_walls(
                                banner_pixels, banner_src_w, banner_src_h,
                                banner_cols, banner_row, banner_col,
                            )
                        except Exception:
                            banner_lines = []
                            banner_rows = 0
                            normals = {}
                    if not banner_lines:
                        banner = _build_banner_str(banner_cols)
                        banner_lines = banner.splitlines() if banner else []
                        banner_rows = len(banner_lines)
                    drops[:] = [self._new_drop(cols) for _ in range(_MAX_DROPS // 3)]
                    frags.clear()
                    splashes.clear()

                if first:
                    sys.stdout.write("\033[H\033[2J")
                    first = False
                sys.stdout.write(
                    self._render_root_frame(
                        rows=rows, cols=cols,
                        banner_lines=banner_lines,
                        banner_cols=banner_cols, banner_rows=banner_rows,
                        banner_row=banner_row, banner_col=banner_col,
                        drops=drops, frags=frags, splashes=splashes,
                        normals=normals, bar=bar,
                    )
                )
                sys.stdout.flush()

                if select.select([fd], [], [], FRAME_S)[0]:
                    raw = os.read(fd, 1)
                    if raw == b"\x03":
                        raise KeyboardInterrupt
                    if raw in {b"\r", b"\n"}:
                        continue
                    try:
                        choice = raw.decode(errors="ignore").lower()
                    except UnicodeDecodeError:
                        continue
                    if choice in valid_keys:
                        return choice
        finally:
            if not renderer_active and old_tty is not None:
                termios.tcsetattr(fd, termios.TCSADRAIN, old_tty)
                sys.stdout.write("\033[?1049l\033[?25h")
                sys.stdout.flush()

    # ── Submenu rain lifecycle ────────────────────────────────────────────

    def ensure_submenu_rain(self, floor_row: int, cols: int) -> None:
        st = self._submenu_rain
        banner_cols = max(20, int(max(30, min(cols * 55 // 100, 60)) * 0.70))
        banner_col = max(0, (cols - banner_cols) // 2)
        banner_row = 1 if floor_row > 8 else 0
        key = (floor_row, cols, banner_cols, banner_row, banner_col)
        if st["layout_key"] == key:
            return
        st["layout_key"] = key
        st["banner_cols"] = banner_cols
        st["banner_col"] = banner_col
        st["banner_row"] = banner_row
        st["banner_rows"] = 0
        st["normals"] = {}
        if _rain_load_banner_pixels is not None and _rain_render_banner is not None:
            try:
                pixels, src_w, src_h = _rain_load_banner_pixels()
                st["normals"], st["banner_rows"] = self._build_banner_walls(
                    pixels, src_w, src_h, banner_cols, banner_row, banner_col,
                )
            except Exception:
                pass
        st["drops"] = [self._new_drop(cols) for _ in range(_SUBMENU_DROPS // 3)]
        st["frags"] = []
        st["splashes"] = []
        st["bolt"] = None
        st["bolt_frames"] = 0
        st["bolt_cooldown"] = random.randint(60, _BOLT_COOLDOWN_MIN)

    def tick_submenu_rain(self, floor_row: int, cols: int) -> None:
        st = self._submenu_rain
        next_drops, new_frags, new_splashes = self._update_drops(
            st["drops"], floor_row + 2, cols, st["normals"],
            ground=floor_row, max_drops=_SUBMENU_DROPS,
        )
        st["drops"] = next_drops
        st["frags"] = self._update_frags(
            st["frags"] + new_frags, floor_row + 2, cols, st["normals"],
            ground=floor_row,
        )
        st["splashes"].extend(new_splashes)
        del st["splashes"][:-12]

        if st["bolt"] is not None:
            st["bolt_frames"] -= 1
            if st["bolt_frames"] <= 0:
                st["bolt"] = None
                st["bolt_cooldown"] = random.randint(_BOLT_COOLDOWN_MIN, _BOLT_COOLDOWN_MAX)
        else:
            st["bolt_cooldown"] -= 1
            if st["bolt_cooldown"] <= 0:
                st["bolt"] = self._generate_bolt(cols, floor_row)
                st["bolt_frames"] = _BOLT_FRAMES

    def render_submenu_rain(self, floor_row: int, cols: int, rule_line: str) -> None:
        st = self._submenu_rain
        br = st["banner_row"]
        bc = st["banner_col"]
        bw = st["banner_cols"]
        bh = st["banner_rows"]
        banner_end = br + bh
        bolt = st["bolt"]
        bolt_age = _BOLT_FRAMES - st["bolt_frames"]

        parts: list[str] = []

        for idx, line in enumerate(self._renderer._banner_lines):
            if idx >= floor_row:
                break
            vis = _ansi_len(line)
            parts.append(f"\033[{idx + 1};1H{line}{' ' * max(0, cols - vis)}")
        for idx in range(len(self._renderer._banner_lines), floor_row):
            parts.append(f"\033[{idx + 1};1H{' ' * cols}")

        for drop in st["drops"]:
            row, col = int(drop[1]), int(drop[0])
            if br <= row < banner_end and bc <= col < bc + bw:
                continue
            if 0 <= row < floor_row and 0 <= col < cols:
                ch = "\\" if drop[2] > 0.18 else "|"
                parts.append(f"\033[{row + 1};{col + 1}H\033[0m{ch}")

        for frag in st["frags"]:
            row, col = int(frag[1]), int(frag[0])
            if br <= row < banner_end and bc <= col < bc + bw:
                continue
            if 0 <= row < floor_row and 0 <= col < cols:
                brightness = frag[4] / _FRAG_LIFE
                if brightness > 0.65:
                    ch = "\033[0;1m*\033[0m"
                elif brightness > 0.35:
                    ch = "\033[0;1m.\033[0m"
                elif brightness > 0.14:
                    ch = "\033[0m."
                else:
                    continue
                parts.append(f"\033[{row + 1};{col + 1}H{ch}")

        if bolt is not None:
            if bolt_age == 0:
                bolt_style = "\033[97;1m"
            elif bolt_age == 1:
                bolt_style = "\033[37;1m"
            elif bolt_age == 2:
                bolt_style = "\033[36m"
            else:
                bolt_style = "\033[2;36m"
            for row, col in bolt:
                if 0 <= row < floor_row and 0 <= col < cols:
                    parts.append(f"\033[{row + 1};{col + 1}H{bolt_style}\u2502\033[0m")

        vis = _ansi_len(rule_line)
        parts.append(f"\033[{floor_row + 1};1H\033[0m{rule_line}{' ' * max(0, cols - vis)}")

        sys.stdout.write("".join(parts))
        sys.stdout.flush()

    def run_scanline_reveal(self, floor_row: int, cols: int) -> None:
        import select as _sel
        banner_lines = self._renderer._banner_lines
        total_rows = min(floor_row, len(banner_lines))
        if total_rows < 1:
            return
        fd = sys.stdin.fileno()
        for scan_row in range(total_rows):
            parts: list[str] = []
            for idx in range(scan_row):
                line = banner_lines[idx] if idx < len(banner_lines) else ""
                vis = _ansi_len(line)
                parts.append(f"\033[{idx + 1};1H{line}{' ' * max(0, cols - vis)}")
            if scan_row < len(banner_lines):
                line = banner_lines[scan_row]
                vis = _ansi_len(line)
                parts.append(f"\033[{scan_row + 1};1H{line}{' ' * max(0, cols - vis)}")
                bar = f"\033[{scan_row + 1};1H\033[97;1m\u2594{' ' * (cols - 1)}\033[0m"
                parts.append(bar)
            for idx in range(scan_row + 1, floor_row):
                parts.append(f"\033[{idx + 1};1H{' ' * cols}")
            sys.stdout.write("".join(parts))
            sys.stdout.flush()
            if _sel.select([fd], [], [], FRAME_S)[0]:
                return
        parts = []
        for idx in range(total_rows):
            line = banner_lines[idx] if idx < len(banner_lines) else ""
            vis = _ansi_len(line)
            parts.append(f"\033[{idx + 1};1H{line}{' ' * max(0, cols - vis)}")
        sys.stdout.write("".join(parts))
        sys.stdout.flush()

    # ── Private physics/render helpers ────────────────────────────────────

    def _status_icon(self, status: str) -> str:
        if status == "ok":
            return f"{_ANSI_GREEN}\u25cf{_ANSI_RESET}"
        if status == "fail":
            return f"{_ANSI_RED}\u25cf{_ANSI_RESET}"
        return f"{_ANSI_YELLOW}\u25cf{_ANSI_RESET}"

    def _status_lines(self, bar: _health.StatusBar) -> list[str]:
        stale_suffix = {
            "fresh": "",
            "recent": "  (recent)",
            "stale": "  \u26a0stale",
            "unknown": "",
        }.get(bar.staleness, "")
        mp_cand = _health.PROBE_ICONS.get(bar.mp_candidate, "?")
        mp_curr = _health.PROBE_ICONS.get(bar.mp_current, "?")
        return [
            (
                f"game {self._status_icon(bar.game)}  "
                f"svr {self._status_icon(bar.server)}  "
                f"venv {self._status_icon(bar.venv)}  "
                f"mp:{mp_cand}/{mp_curr}{stale_suffix}"
            ),
            (
                "features  "
                f"GAME {self._status_icon(bar.game)}  "
                f"MAP {self._status_icon(bar.map_tools)}  "
                f"MP {self._status_icon(bar.multiplayer)}  "
                f"ASSET {self._status_icon(bar.asset_pipeline)}"
            ),
            "\u25cf=ok  \u25cf=attention  \u25cf=fail    game=game binary  svr=server binary  venv=python env  mp=staging/live",
        ]

    @staticmethod
    def _new_drop(cols: int) -> list[float]:
        return [
            float(random.randint(0, max(0, cols - 1))),
            float(random.randint(-14, -1)),
            max(0.0, _VX + random.gauss(0, 0.12)),
            random.uniform(*_VY),
        ]

    @staticmethod
    def _reflect_damp(vx: float, vy: float, nc: float, nr: float) -> tuple[float, float]:
        dot = vx * nc + vy * nr
        return (
            (vx - 2 * dot * nc) * _BOUNCE_DAMP,
            (vy - 2 * dot * nr) * _BOUNCE_DAMP,
        )

    def _build_banner_walls(
        self,
        pixels: list[tuple[int, int, int, int]],
        src_w: int,
        src_h: int,
        banner_cols: int,
        row_off: int,
        col_off: int,
    ) -> tuple[dict[tuple[int, int], tuple[float, float]], int]:
        tgt_h = max(2, int(src_h * banner_cols / src_w))
        if tgt_h % 2:
            tgt_h += 1
        banner_rows = tgt_h // 2

        opaque: set[tuple[int, int]] = set()
        for grid_row in range(banner_rows):
            for grid_col in range(banner_cols):
                src_x = int(grid_col * src_w / banner_cols)
                for py in range(2):
                    src_y = int((grid_row * 2 + py) * src_h / tgt_h)
                    if src_y < src_h and pixels[src_y * src_w + src_x][3] >= 16:
                        opaque.add((grid_row, grid_col))
                        break

        normals: dict[tuple[int, int], tuple[float, float]] = {}
        for row, col in opaque:
            up = (row - 1, col) not in opaque
            down = (row + 1, col) not in opaque
            left = (row, col - 1) not in opaque
            right = (row, col + 1) not in opaque
            if not (up or down or left or right):
                normals[(row + row_off, col + col_off)] = (0.0, -1.0)
                continue
            normal_row = -1 if up else (1 if down else 0)
            normal_col = (-1 if left else 0) + (1 if right else 0)
            if normal_row == 0 and normal_col == 0:
                normal_row = -1
            mag = math.sqrt(normal_row ** 2 + normal_col ** 2)
            normals[(row + row_off, col + col_off)] = (normal_col / mag, normal_row / mag)

        return normals, banner_rows

    def _spawn_frags(
        self, x: float, y: float, vx: float, vy: float,
        normal_col: float, normal_row: float,
    ) -> list[list[float]]:
        reflected_x, reflected_y = self._reflect_damp(vx, vy, normal_col, normal_row)
        return [
            [x, y, reflected_x + random.gauss(0, 0.22), reflected_y + random.gauss(0, 0.18), float(_FRAG_LIFE)]
            for _ in range(_FRAG_COUNT)
        ]

    def _update_drops(
        self,
        drops: list[list[float]],
        rows: int,
        cols: int,
        normals: dict[tuple[int, int], tuple[float, float]],
        ground: int | None = None,
        max_drops: int | None = None,
    ) -> tuple[list[list[float]], list[list[float]], list[int]]:
        if ground is None:
            ground = rows - 2
        if max_drops is None:
            max_drops = _MAX_DROPS
        alive: list[list[float]] = []
        frags: list[list[float]] = []
        splashes: list[int] = []
        for drop in drops:
            drop[3] += _GRAVITY
            next_x = drop[0] + drop[2]
            next_y = drop[1] + drop[3]
            if next_x < 0 or next_x >= cols:
                continue
            row, col = int(next_y), int(next_x)
            if row >= ground:
                splashes.append(int(drop[0]))
                continue
            hit = normals.get((row, col))
            if hit:
                frags.extend(self._spawn_frags(drop[0], drop[1], drop[2], drop[3], hit[0], hit[1]))
                continue
            drop[0] = next_x
            drop[1] = next_y
            alive.append(drop)

        while len(alive) < max_drops:
            alive.append(self._new_drop(cols))
        return alive, frags, splashes

    def _update_frags(
        self,
        frags: list[list[float]],
        rows: int,
        cols: int,
        normals: dict[tuple[int, int], tuple[float, float]],
        ground: int | None = None,
    ) -> list[list[float]]:
        if ground is None:
            ground = rows - 2
        alive: list[list[float]] = []
        for frag in frags:
            frag[3] += _GRAVITY * 1.30
            frag[0] += frag[2]
            frag[1] += frag[3]
            frag[4] -= 1
            row, col = int(frag[1]), int(frag[0])
            if frag[4] <= 0 or row >= ground or col < 0 or col >= cols:
                continue
            hit = normals.get((row, col))
            if hit and frag[2] * hit[0] + frag[3] * hit[1] < 0:
                frag[2], frag[3] = self._reflect_damp(frag[2], frag[3], hit[0], hit[1])
                frag[0] += hit[0] * 0.6
                frag[1] += hit[1] * 0.6
            alive.append(frag)
        if len(alive) > _MAX_FRAGS:
            alive = alive[-_MAX_FRAGS:]
        return alive

    def _render_frags(self, frags: list[list[float]]) -> str:
        parts: list[str] = []
        for frag in frags:
            row, col = int(frag[1]), int(frag[0])
            brightness = frag[4] / _FRAG_LIFE
            if brightness > 0.65:
                char = "\033[1m*\033[0m"
            elif brightness > 0.35:
                char = "\033[1m.\033[0m"
            elif brightness > 0.14:
                char = "."
            else:
                continue
            parts.append(f"\033[{row + 1};{col + 1}H{char}")
        return "".join(parts)

    @staticmethod
    def _visible_width(text: str) -> int:
        return len(re.sub(r"\033\[[0-9;?]*[A-Za-z]", "", text))

    @staticmethod
    def _center_col(text: str, cols: int) -> int:
        return max(1, (cols - RainEngine._visible_width(text)) // 2 + 1)

    def _overlay_lines(self, start_row: int, cols: int, lines: list[str]) -> str:
        parts: list[str] = []
        visible_width = max((self._visible_width(line) for line in lines), default=0)
        col = max(1, (cols - visible_width) // 2 + 1)
        for offset, line in enumerate(lines):
            row = start_row + offset
            if row < 0:
                continue
            parts.append(f"\033[{row + 1};{col}H\033[2K{line}")
        return "".join(parts)

    def _menu_lines(self, bar: _health.StatusBar) -> list[str]:
        label_width = max(self._visible_width(name) for _key, name, _desc in self._menu_items)
        key_width = 3

        def _pad_visible(text: str, width: int) -> str:
            return text + (" " * max(0, width - self._visible_width(text)))

        lines = [
            f"{_ANSI_DIM}SCRIPTS LAUNCHER{_ANSI_RESET}",
            *self._status_lines(bar),
            "",
        ]
        for key, name, desc in self._menu_items:
            badge = f"{_ANSI_RED}{_ANSI_BOLD}[{key}]{_ANSI_RESET}"
            key_col = _pad_visible(badge, key_width)
            label = _pad_visible(f"{_ANSI_BOLD}{name}{_ANSI_RESET}", label_width)
            suffix = f"  {_ANSI_DIM}{desc}{_ANSI_RESET}" if desc else ""
            lines.append(f"{key_col}  {label}{suffix}")
        return lines

    def _render_root_frame(
        self,
        *,
        rows: int, cols: int,
        banner_lines: list[str],
        banner_cols: int, banner_rows: int,
        banner_row: int, banner_col: int,
        drops: list[list[float]], frags: list[list[float]], splashes: list[int],
        normals: dict[tuple[int, int], tuple[float, float]],
        bar: _health.StatusBar,
    ) -> str:
        ground = max(0, rows - 2)
        grid = [[" "] * cols for _ in range(rows)]
        if ground < rows:
            for col in range(cols):
                grid[ground][col] = "_"

        next_drops, new_frags, new_splashes = self._update_drops(drops, rows, cols, normals)
        drops[:] = next_drops
        frags[:] = self._update_frags(frags + new_frags, rows, cols, normals)
        splashes.extend(new_splashes)

        banner_end = banner_row + banner_rows
        for drop in drops:
            row, col = int(drop[1]), int(drop[0])
            if banner_row <= row < banner_end and banner_col <= col < banner_col + banner_cols:
                continue
            if 0 <= row < rows and grid[row][col] == " ":
                grid[row][col] = "\\" if drop[2] > 0.18 else "|"

        for col in splashes:
            if 0 <= col < cols and ground < rows:
                grid[ground][col] = random.choice(["'", "."])
        del splashes[:-16]

        out = "\033[H" + "\r\n".join("".join(row) for row in grid)
        for idx, line in enumerate(banner_lines):
            out += f"\033[{banner_row + idx + 1};{banner_col + 1}H{line}"

        menu_start = min(max(banner_row + banner_rows + 1, 1), max(1, rows - len(self._menu_items) - 5))
        out += self._overlay_lines(menu_start, cols, self._menu_lines(bar))
        return out + self._render_frags(frags)

    @staticmethod
    def _generate_bolt(cols: int, floor_row: int) -> list[tuple[int, int]]:
        col = random.randint(cols // 4, 3 * cols // 4)
        positions: list[tuple[int, int]] = []
        for row in range(0, floor_row):
            positions.append((row, col))
            col += random.choice([-2, -1, 0, 0, 1, 2])
            col = max(1, min(cols - 2, col))
            if random.random() < 0.12:
                branch_col = col + random.choice([-3, -2, 2, 3])
                if 0 <= branch_col < cols:
                    positions.append((row, branch_col))
        return positions
