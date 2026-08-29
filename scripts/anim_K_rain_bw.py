"""K: Scanline → diagonal rain + letter bounce — black and white version.

Identical physics to I (anim_I_rain.py):
  Phase 1 — Scanline (plays once).
  Phase 2 — Rain loop with diagonal drops, letter-impact bounces,
             contour drips, and ground splashes.

Color changes from I:
  - All RGB gradients replaced with greyscale (R == G == B).
  - Drop streak: white tip → light grey → dark grey tail.
  - Atmospheric background: near-black grey instead of midnight blue.
  - Burst arc particles: bright-white → grey fade.
  - Contour drip dots: mid-grey → dark grey fade.
  - Ground line and splash: grey ▄ / grey ·.

Resize during either phase restarts from Phase 1.

Run: python3 testing/anim_K_rain_bw.py
"""
from __future__ import annotations
import os, random, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from _common import (
    AltScreen, banner_grid_dims, brighten_toward_white,
    is_interactive, key_ready, load_banner_pixels, render_banner,
    sigwinch_flag, sigwinch_restore,
    target_cols, tty_enter_raw, tty_restore,
)

ROW_DELAY_S = 0.040
FRAME_S     = 0.040
MAX_DROPS   = 50
MAX_BURSTS  = 15
MAX_DRIPS   = 20
BASE_LEAN   = 0.35
GRAVITY     = 0.10


# ── Scanline colour_fn ────────────────────────────────────────────────────────

def _scanline_fn(cur: int):
    def fn(r, g, b, a, gx, gy, gw, gh, _c=cur):
        row = gy // 2
        if row > _c:   return 0, 0, 0, 0
        if row == _c:  return brighten_toward_white(r, g, b, a, t=0.40)
        return r, g, b, a
    return fn


# ── Rain colour palette — greyscale ──────────────────────────────────────────

def _drop_color(dist: int, length: int, brightness: float) -> tuple[int, int, int]:
    """White tip → light grey → dark grey tail, dimmed by brightness."""
    t = dist / max(1, length - 1)
    if t < 0.15:
        v = 1.0 - (t / 0.15) * 0.15
        c = int(230 + 25 * v)        # 230 → 255
    elif t < 0.55:
        s = (t - 0.15) / 0.40
        c = int(220 - s * 120)       # 220 → 100
    else:
        s = (t - 0.55) / 0.45
        c = int(100 - s * 65)        # 100 → 35
    c = int(c * brightness)
    return c, c, c


# ── Shadow mask (letter borders) ─────────────────────────────────────────────

def _build_shadow_mask(pixels, src_w, src_h, tgt_w, tgt_h):
    """1-pixel black border around opaque banner letters."""
    opaque = bytearray(tgt_w * tgt_h)
    for gy in range(tgt_h):
        sy = int(gy * src_h / tgt_h)
        for gx in range(tgt_w):
            sx = int(gx * src_w / tgt_w)
            if pixels[sy * src_w + sx][3] >= 16:
                opaque[gy * tgt_w + gx] = 1
    shadow = bytearray(tgt_w * tgt_h)
    for gy in range(tgt_h):
        for gx in range(tgt_w):
            if opaque[gy * tgt_w + gx]:
                continue
            for dy in (-1, 0, 1):
                ny = gy + dy
                if ny < 0 or ny >= tgt_h:
                    continue
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx = gx + dx
                    if 0 <= nx < tgt_w and opaque[ny * tgt_w + nx]:
                        shadow[gy * tgt_w + gx] = 1
    return shadow


# ── Opaque grid ───────────────────────────────────────────────────────────────

def _build_opaque_grid(pixels, src_w, src_h, tgt_w, b_rows):
    tgt_h = b_rows * 2
    opaque: set[tuple[int, int]] = set()
    for gr in range(b_rows):
        for gx in range(tgt_w):
            sx = int(gx * src_w / tgt_w)
            for py in range(2):
                sy = int((gr * 2 + py) * src_h / tgt_h)
                if sy < src_h and pixels[sy * src_w + sx][3] >= 16:
                    opaque.add((gx, gr))
                    break
    return opaque


# ── Pixel map ─────────────────────────────────────────────────────────────────

def _build_pixel_map(drops, cols, b_rows):
    pmap: dict[tuple[int, int], tuple[int, int, float]] = {}
    for drop in drops:
        tip_x = drop[0];  tip_y = int(drop[1])
        length = drop[2];  dx = drop[4];  brt = drop[5]
        for d in range(length):
            col = int(tip_x - dx * d)
            row = tip_y - d
            if 0 <= col < cols and 0 <= row < b_rows:
                key = (col, row)
                if key not in pmap or pmap[key][0] > d:
                    pmap[key] = (d, length, brt)
    return pmap


# ── Rain color_fn — greyscale ─────────────────────────────────────────────────

_RAIN_BG = (10, 10, 10)   # near-black grey


def _make_rain_fn(pmap, tgt_w, shadow):
    def fn(r, g, b, a, gx, gy, gw, gh, _pm=pmap, _sh=shadow, _tw=tgt_w):
        if a >= 16:
            return r, g, b, a
        if _sh[gy * _tw + gx]:
            return 0, 0, 0, 255
        hit = _pm.get((gx, gy // 2))
        if hit:
            dr, dg, db = _drop_color(hit[0], hit[1], hit[2])
            return dr, dg, db, 255
        return _RAIN_BG[0], _RAIN_BG[1], _RAIN_BG[2], 255
    return fn


# ── Drop particles ────────────────────────────────────────────────────────────

def _new_drop(cols, b_rows):
    length = random.choices([1, 2, 3], weights=[65, 25, 10])[0]
    return [
        float(random.randint(0, cols - 1)),
        float(random.randint(-8, b_rows - 2)),
        length,
        random.uniform(0.35, 3.0),
        BASE_LEAN + random.uniform(-0.08, 0.08),
        random.uniform(0.35, 1.0),
    ]


def _update_drops(drops, cols, b_rows, opaque_grid):
    alive          = []
    ground_impacts: list[int]                            = []
    letter_impacts: list[tuple[int, int, float, float]]  = []

    for drop in drops:
        drop[0] += drop[4];  drop[1] += drop[3]
        tip_col = int(drop[0]);  tip_row = int(drop[1])

        if tip_col < 0 or tip_col >= cols:
            continue
        if tip_row >= b_rows:
            ground_impacts.append(tip_col)
        elif tip_row >= 0 and (tip_col, tip_row) in opaque_grid:
            letter_impacts.append((tip_col, tip_row, drop[4], drop[3]))
        else:
            alive.append(drop)

    while len(alive) < MAX_DROPS:
        alive.append(_new_drop(cols, b_rows))
    return alive, ground_impacts, letter_impacts


# ── Ground splash particles ───────────────────────────────────────────────────

def _new_ground_splashes(x):
    n = random.randint(2, 4)
    out = []
    for _ in range(n):
        side = random.choice([-1, 1])
        out.append([float(x), side * random.uniform(0.5, 2.8), random.randint(4, 11)])
    return out


def _update_ground_splashes(splashes):
    alive = []
    for sp in splashes:
        sp[0] += sp[1];  sp[2] -= 1
        if sp[2] > 0:
            alive.append(sp)
    return alive


# ── Strategy A: Reflected burst particles — greyscale ────────────────────────

def _new_burst_splashes(x, gr, drop_dx, drop_dy):
    n = random.randint(2, 3)
    out = []
    for _ in range(n):
        vx = -drop_dx * random.uniform(0.3, 0.9) + random.uniform(-0.4, 0.4)
        vy = -abs(drop_dy) * random.uniform(0.3, 0.7)
        out.append([float(x), float(gr), vx, vy, random.randint(4, 8)])
    return out


def _update_burst_splashes(bursts, cols, b_rows, opaque_grid):
    alive = []
    for sp in bursts:
        sp[0] += sp[2];  sp[1] += sp[3];  sp[3] += GRAVITY;  sp[4] -= 1
        gx = int(sp[0]);  gy = int(sp[1])
        if sp[4] <= 0 or gx < 0 or gx >= cols or gy < 0 or gy >= b_rows:
            continue
        if (gx, gy) in opaque_grid:
            continue
        alive.append(sp)
    return alive


def _render_burst_splashes(bursts, b_rows):
    parts = []
    for sp in bursts:
        gx = int(sp[0]);  gy = int(sp[1]);  lif = sp[4]
        if gx < 0 or gy < 0 or gy >= b_rows:
            continue
        t  = lif / 8.0
        c  = int(80 + 175 * t)   # 80 → 255 grey
        ch = '*' if t > 0.7 else '·'
        parts.append(f"\033[{gy+1};{gx+1}H\033[38;2;{c};{c};{c}m{ch}\033[0m")
    return "".join(parts)


# ── Strategy B: Contour-walking drip particles — greyscale ───────────────────

def _new_drip(x, gr):
    return [float(x), float(gr), random.randint(14, 28)]


def _step_drip(sp, cols, b_rows, opaque_grid):
    x = int(sp[0]);  y = int(sp[1])
    lr = [1, -1] if random.random() < 0.5 else [-1, 1]
    for dx, dy in [(0, 1), (lr[0], 1), (lr[1], 1), (lr[0], 0), (lr[1], 0)]:
        nx, ny = x + dx, y + dy
        if 0 <= nx < cols and 0 <= ny < b_rows and (nx, ny) not in opaque_grid:
            sp[0] = float(nx);  sp[1] = float(ny)
            return True
    return False


def _update_drips(drips, cols, b_rows, opaque_grid):
    alive = []
    for sp in drips:
        sp[2] -= 1
        if sp[2] <= 0:
            continue
        if _step_drip(sp, cols, b_rows, opaque_grid):
            alive.append(sp)
    return alive


def _render_drips(drips, b_rows, opaque_grid):
    parts = []
    for sp in drips:
        gx = int(sp[0]);  gy = int(sp[1]);  lif = sp[2]
        if gx < 0 or gy < 0 or gy >= b_rows:
            continue
        if (gx, gy) in opaque_grid:
            continue
        t  = min(1.0, lif / 14.0)
        c  = int(55 + 130 * t)   # 55 → 185 grey
        parts.append(f"\033[{gy+1};{gx+1}H\033[38;2;{c};{c};{c}m·\033[0m")
    return "".join(parts)


# ── Ground line render — greyscale ────────────────────────────────────────────

def _render_ground(b_rows, cols, gsplashes, impacts):
    term_row   = b_rows + 1
    impact_set = set(impacts)
    smap: dict[int, int] = {}
    for sp in gsplashes:
        gx = int(sp[0])
        if 0 <= gx < cols:
            smap[gx] = max(smap.get(gx, 0), sp[2])

    cells = []
    for gx in range(cols):
        if gx in impact_set:
            cells.append("\033[38;2;220;220;220m*\033[0m")
        elif gx in smap:
            t  = smap[gx] / 11.0
            c  = int(80 + 140 * t)   # 80 → 220 grey
            cells.append(f"\033[38;2;{c};{c};{c}m·\033[0m")
        else:
            cells.append("\033[38;2;60;60;60m\033[48;2;8;8;8m▄\033[0m")

    return f"\033[{term_row};1H" + "".join(cells)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not is_interactive():
        return

    pixels, src_w, src_h = load_banner_pixels()
    if not pixels:
        return

    with AltScreen():
        fd, old_tty = tty_enter_raw()
        resize, old_sig = sigwinch_flag()
        try:
            while True:
                cols      = target_cols()
                _, b_rows = banner_grid_dims(src_w, src_h, cols)

                # ── Phase 1: scanline ─────────────────────────────────────────
                cur = 0;  needs_clear = True;  restarted = False

                while cur < b_rows:
                    if resize[0]:
                        resize[0] = False;  restarted = True;  break
                    if key_ready(fd):
                        if os.read(fd, 1) in (b"q", b"Q", b"\x03"):
                            return
                    prefix = "\033[H\033[2J" if needs_clear else "\033[H"
                    needs_clear = False
                    banner, _ = render_banner(pixels, src_w, src_h, cols, _scanline_fn(cur))
                    sys.stdout.write(prefix + banner)
                    sys.stdout.flush()
                    time.sleep(ROW_DELAY_S)
                    cur += 1

                if restarted:
                    continue

                banner, _ = render_banner(pixels, src_w, src_h, cols)
                sys.stdout.write("\033[H\033[2J" if needs_clear else "\033[H")
                sys.stdout.write(banner)
                sys.stdout.flush()
                time.sleep(0.20)

                # ── Phase 2: rain ─────────────────────────────────────────────
                tgt_h       = b_rows * 2
                shadow      = _build_shadow_mask(pixels, src_w, src_h, cols, tgt_h)
                opaque_grid = _build_opaque_grid(pixels, src_w, src_h, cols, b_rows)
                drops       = [_new_drop(cols, b_rows) for _ in range(MAX_DROPS // 2)]
                gsplashes: list = []
                lbursts:   list = []
                ldrips:    list = []
                needs_clear = True

                while True:
                    if resize[0]:
                        resize[0] = False;  break
                    if key_ready(fd):
                        if os.read(fd, 1) in (b"q", b"Q", b"\x03"):
                            return

                    drops, g_impacts, l_impacts = _update_drops(
                        drops, cols, b_rows, opaque_grid
                    )
                    for ix in g_impacts:
                        gsplashes.extend(_new_ground_splashes(ix))
                    for ix, iy, ddx, ddy in l_impacts:
                        lbursts.extend(_new_burst_splashes(ix, iy, ddx, ddy))
                        ldrips.append(_new_drip(ix, iy))

                    gsplashes = _update_ground_splashes(gsplashes)
                    lbursts   = _update_burst_splashes(lbursts, cols, b_rows, opaque_grid)
                    ldrips    = _update_drips(ldrips, cols, b_rows, opaque_grid)

                    if len(lbursts) > MAX_BURSTS:
                        lbursts = lbursts[-MAX_BURSTS:]
                    if len(ldrips) > MAX_DRIPS:
                        ldrips = ldrips[-MAX_DRIPS:]

                    pmap     = _build_pixel_map(drops, cols, b_rows)
                    color_fn = _make_rain_fn(pmap, cols, shadow)
                    banner, _ = render_banner(pixels, src_w, src_h, cols, color_fn)

                    prefix = "\033[H\033[2J" if needs_clear else "\033[H"
                    needs_clear = False

                    drp_str = _render_drips(ldrips, b_rows, opaque_grid)
                    bst_str = _render_burst_splashes(lbursts, b_rows)
                    gnd_str = _render_ground(b_rows, cols, gsplashes, g_impacts)
                    sys.stdout.write(prefix + banner + drp_str + bst_str + gnd_str)
                    sys.stdout.flush()

                    time.sleep(FRAME_S)

        finally:
            tty_restore(fd, old_tty)
            sigwinch_restore(old_sig)


if __name__ == "__main__":
    main()
