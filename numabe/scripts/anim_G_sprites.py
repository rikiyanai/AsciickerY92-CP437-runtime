"""G: Banner fire with floating white embers and letter shadow outlines.

Doom heat-propagation fills the banner's transparent pixels with orange fire.
Fire is dimmed to 65% so the logo text pops against it.
Transparent pixels adjacent to opaque letters are blacked out (shadow outline),
making letters pop against the fire background.
White ember particles spawn from the hot bottom edge and drift upward,
fading as they rise through the banner.

Loops until 'q' or Ctrl-C.

Run: python3 testing/anim_G_sprites.py
"""
from __future__ import annotations
import os, random, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from _common import (
    AltScreen, banner_grid_dims, is_interactive, key_ready,
    load_banner_pixels, render_banner,
    sigwinch_flag, sigwinch_restore,
    target_cols, tty_enter_raw, tty_restore,
)

FRAME_S    = 0.040   # 40 ms — ~25 fps
MAX_EMBERS = 40


# ── Orange fire colour palette ───────────────────────────────────────────────

def _orange(h: int) -> tuple[int, int, int]:
    if h < 40:
        t = h / 40
        return int(t * 190), 0, 0
    elif h < 120:
        t = (h - 40) / 80
        return int(190 + t * 65), int(t * 165), 0
    elif h < 200:
        t = (h - 120) / 80
        return 255, int(165 + t * 70), 0
    else:
        t = (h - 200) / 55
        return 255, min(255, int(235 + t * 20)), 0


# ── Doom fire step ───────────────────────────────────────────────────────────

def _init_grid(w: int, h: int) -> list[int]:
    g = [0] * (w * h)
    for x in range(w):
        g[(h - 1) * w + x] = 255
    return g


def _step(grid: list[int], w: int, h: int) -> None:
    for x in range(w):
        grid[(h - 1) * w + x] = random.randint(180, 255)
    for y in range(h - 1):
        for x in range(w):
            sx = (x + random.randint(-1, 1)) % w
            decay = random.randint(15, 45)   # steep: needs ~17/step avg to span 12 rows
            grid[y * w + x] = max(0, grid[(y + 1) * w + sx] - decay)


# ── Shadow mask (computed once per resize) ───────────────────────────────────

def _build_shadow_mask(
    pixels: list, src_w: int, src_h: int, tgt_w: int, tgt_h: int
) -> bytearray:
    """Return flat bytearray (tgt_w * tgt_h).
    1 = transparent pixel adjacent to an opaque letter pixel → render black.
    0 = normal fire or opaque.
    """
    # Step 1: opaque map in target pixel space
    opaque = bytearray(tgt_w * tgt_h)
    for gy in range(tgt_h):
        sy = int(gy * src_h / tgt_h)
        for gx in range(tgt_w):
            sx = int(gx * src_w / tgt_w)
            if pixels[sy * src_w + sx][3] >= 16:
                opaque[gy * tgt_w + gx] = 1

    # Step 2: shadow = transparent pixels within 1 of an opaque pixel
    shadow = bytearray(tgt_w * tgt_h)
    for gy in range(tgt_h):
        for gx in range(tgt_w):
            if opaque[gy * tgt_w + gx]:
                continue   # opaque pixels are not shadowed
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


# ── Fire-composite colour_fn ──────────────────────────────────────────────────

def _make_fire_fn(grid: list[int], fire_w: int, fire_h: int, shadow: bytearray):
    """Transparent pixels → dimmed orange fire, or black if adjacent to a letter.
    Opaque pixels (letters themselves) → unchanged."""
    def fn(r, g, b, a, gx, gy, gw, gh,
           _g=grid, _fw=fire_w, _fh=fire_h, _sh=shadow):
        if a >= 16:
            return r, g, b, a
        # Black outline: transparent pixels bordering letter pixels
        if _sh[gy * _fw + gx]:
            return 0, 0, 0, 255
        fy = min(_fh - 1, gy // 2)
        fx = min(_fw - 1, gx)
        h  = _g[fy * _fw + fx]
        if h < 8:
            return 0, 0, 0, 0
        # Scale to 65% so the logo letters naturally pop over the background fire
        fr, fg_c, fb = _orange(int(h * 0.65))
        return fr, fg_c, fb, 255
    return fn


# ── Ember particles ──────────────────────────────────────────────────────────
# Each ember: [x, y, heat, vx, vy]  (y=0 is top row; y increases downward)

def _new_ember(cols: int, b_rows: int) -> list:
    return [
        float(random.randint(0, cols - 1)),
        float(b_rows - 1),           # start at bottom banner row
        float(random.randint(160, 255)),
        random.uniform(-0.3, 0.3),   # horizontal drift
        random.uniform(-0.4, -0.9),  # upward velocity (y decreases)
    ]


def _update_embers(embers: list, cols: int, b_rows: int) -> list:
    alive = []
    for em in embers:
        em[0] += em[3] + random.uniform(-0.12, 0.12)
        em[1] += em[4]
        em[2] -= random.uniform(8, 22)
        if em[2] >= 15 and em[1] >= -1:
            alive.append(em)
    if len(alive) < MAX_EMBERS and random.random() < 0.6:
        alive.append(_new_ember(cols, b_rows))
    return alive


def _render_embers(embers: list, cols: int) -> str:
    parts = []
    for em in embers:
        row = int(em[1]) + 1   # 1-indexed terminal row
        col = int(em[0]) + 1   # 1-indexed terminal col
        heat = int(em[2])
        if heat < 15 or row < 1 or col < 1 or col > cols:
            continue
        # White embers that warm very slightly at low heat
        t = min(1.0, heat / 255)
        er = 255
        eg = int(255 * t + 210 * (1 - t))   # 255 → 210
        eb = int(255 * t + 190 * (1 - t))   # 255 → 190
        ch = '·' if heat > 120 else '∙'
        parts.append(f"\033[{row};{col}H\033[38;2;{er};{eg};{eb}m{ch}\033[0m")
    return "".join(parts)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not is_interactive():
        return

    pixels, src_w, src_h = load_banner_pixels()
    if not pixels:
        return

    with AltScreen():
        fd, old_tty = tty_enter_raw()
        resize, old_sig = sigwinch_flag()
        try:
            def _setup():
                cols      = target_cols()
                _, b_rows = banner_grid_dims(src_w, src_h, cols)
                tgt_h     = b_rows * 2
                grid      = _init_grid(cols, b_rows)
                shadow    = _build_shadow_mask(pixels, src_w, src_h, cols, tgt_h)
                return cols, b_rows, grid, shadow

            cols, b_rows, grid, shadow = _setup()
            embers: list = []
            needs_clear = True

            while True:
                if resize[0]:
                    resize[0] = False
                    cols, b_rows, grid, shadow = _setup()
                    embers = []
                    needs_clear = True

                if key_ready(fd):
                    ch = os.read(fd, 1)
                    if ch in (b"q", b"Q", b"\x03"):
                        return

                _step(grid, cols, b_rows)
                embers = _update_embers(embers, cols, b_rows)

                color_fn = _make_fire_fn(grid, cols, b_rows, shadow)
                banner, _ = render_banner(pixels, src_w, src_h, cols, color_fn)

                prefix = "\033[H\033[2J" if needs_clear else "\033[H"
                needs_clear = False

                sys.stdout.write(prefix + banner + _render_embers(embers, cols))
                sys.stdout.flush()

                time.sleep(FRAME_S)

        finally:
            tty_restore(fd, old_tty)
            sigwinch_restore(old_sig)


if __name__ == "__main__":
    main()
