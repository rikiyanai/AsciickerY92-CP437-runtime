"""L: Angled ASCII rain on ASCIICKER block letters — rain physics test.

Same rain physics as J (diagonal drops, fragmentation, lightning) applied to
ASCII block-letter art of "ASCIICKER" instead of geometric shapes.

Purpose: preview how rain splash physics look on letter-shaped obstacles before
integrating them into the real launcher banner animation.

No banner asset required — letters are rendered from a pure ASCII block font.
All physics (normals, reflection, fragments, ground splash, lightning) are
identical to anim_J_bounce.py.

'q' / Ctrl-C to quit.  Resize restarts.
Run: python3 testing/anim_L_rain_letters.py
"""
from __future__ import annotations
import math, os, random, shutil, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from _common import (
    AltScreen, is_interactive, key_ready,
    sigwinch_flag, sigwinch_restore,
    tty_enter_raw, tty_restore,
)

FRAME_S       = 0.040
MAX_DROPS     = 60
MAX_FRAGS     = 100
GRAVITY       = 0.08
RAIN_VX       = 0.22
RAIN_VY       = (0.35, 1.80)
BOUNCE_DAMP   = 0.32
FRAG_COUNT    = 3
FRAG_LIFE     = 14
BOLT_LIFE     = 0.75
BOLT_INTERVAL = (6.0, 14.0)

LETTER_GAP = 1   # cols between glyphs


# ── 7-row block font (6 cols per glyph) ──────────────────────────────────────

FONT: dict[str, list[str]] = {
    'A': [
        "  ##  ",
        " #  # ",
        "#    #",
        "######",
        "#    #",
        "#    #",
        "#    #",
    ],
    'S': [
        " #### ",
        "#    #",
        "#     ",
        " #### ",
        "     #",
        "#    #",
        " #### ",
    ],
    'C': [
        " #### ",
        "#    #",
        "#     ",
        "#     ",
        "#     ",
        "#    #",
        " #### ",
    ],
    'I': [
        "######",
        "  ##  ",
        "  ##  ",
        "  ##  ",
        "  ##  ",
        "  ##  ",
        "######",
    ],
    'K': [
        "#    #",
        "#   # ",
        "#  #  ",
        "####  ",
        "#  #  ",
        "#   # ",
        "#    #",
    ],
    'E': [
        "######",
        "#     ",
        "#     ",
        "##### ",
        "#     ",
        "#     ",
        "######",
    ],
    'R': [
        "##### ",
        "#    #",
        "#    #",
        "##### ",
        "# #   ",
        "#  #  ",
        "#   # ",
    ],
}


# ── Letter wall builder ───────────────────────────────────────────────────────

def _text_cells(word: str) -> tuple[set, int]:
    """Return (set of local (row, col) cells, total pixel width)."""
    cells: set = set()
    x = 0
    for ch in word:
        glyph = FONT.get(ch)
        if glyph is None:
            x += 6 + LETTER_GAP
            continue
        gw = len(glyph[0])
        for ri, row_str in enumerate(glyph):
            for ci, px in enumerate(row_str):
                if px != ' ':
                    cells.add((ri, x + ci))
        x += gw + LETTER_GAP
    return cells, x - LETTER_GAP


def _build_letters(rows, cols):
    """Return (normals, chars) dicts — same interface as _build_shapes in J."""
    cells_local, total_w = _text_cells("ASCIICKER")

    col_off = max(0, (cols - total_w) // 2)
    row_off = max(1, rows // 4 - 3)   # upper quarter, a bit of headroom

    cells: set = frozenset(
        (r + row_off, c + col_off)
        for r, c in cells_local
        if 0 <= r + row_off < rows and 0 <= c + col_off < cols
    )

    normals: dict = {}
    chars:   dict = {}

    for (r, c) in cells:
        up    = (r - 1, c) not in cells
        down  = (r + 1, c) not in cells
        left  = (r, c - 1) not in cells
        right = (r, c + 1) not in cells

        is_boundary = up or down or left or right

        if not is_boundary:
            # Interior cell: invisible wall prevents drop tunnelling
            normals[(r, c)] = (0.0, -1.0)
            continue

        # Outward normal: up beats down (rain comes from above)
        nr_s = -1 if up else (1 if down else 0)
        nc_s = (-1 if left else 0) + (1 if right else 0)
        if nr_s == 0 and nc_s == 0:
            nr_s = -1   # fallback
        mag = math.sqrt(nr_s ** 2 + nc_s ** 2)
        normals[(r, c)] = (nc_s / mag, nr_s / mag)

        # Visual char: driven by primary surface direction
        if up:
            if left and not right:   ch = '/'
            elif right and not left: ch = '\\'
            else:                    ch = '-'
        elif down:
            if left and not right:   ch = '\\'
            elif right and not left: ch = '/'
            else:                    ch = '_'
        else:
            ch = '|'

        chars[(r, c)] = ch

    return normals, chars


# ── Physics (identical to anim_J_bounce.py) ───────────────────────────────────

def _reflect_damp(vx, vy, nc, nr):
    dot = vx * nc + vy * nr
    return (vx - 2 * dot * nc) * BOUNCE_DAMP, (vy - 2 * dot * nr) * BOUNCE_DAMP


def _new_drop(cols):
    return [
        float(random.randint(0, max(0, cols - 1))),
        float(random.randint(-14, -1)),
        max(0.0, RAIN_VX + random.gauss(0, 0.12)),
        random.uniform(*RAIN_VY),
    ]


def _update_drops(drops, rows, cols, normals):
    ground = rows - 2
    alive, frags, gsplas = [], [], []
    for d in drops:
        d[3] += GRAVITY
        nx, ny = d[0] + d[2], d[1] + d[3]
        if nx < 0 or nx >= cols:
            continue
        r, c = int(ny), int(nx)
        if r >= ground:
            gsplas.append(int(d[0]));  continue
        hit = normals.get((r, c))
        if hit:
            frags.extend(_spawn_frags(d[0], d[1], d[2], d[3], hit[0], hit[1]))
            continue
        d[0] = nx;  d[1] = ny
        alive.append(d)
    while len(alive) < MAX_DROPS:
        alive.append(_new_drop(cols))
    return alive, frags, gsplas


def _spawn_frags(x, y, vx, vy, nc, nr):
    rvx, rvy = _reflect_damp(vx, vy, nc, nr)
    return [[x, y, rvx + random.gauss(0, 0.22),
                   rvy + random.gauss(0, 0.18), FRAG_LIFE]
            for _ in range(FRAG_COUNT)]


def _update_frags(frags, rows, cols, normals):
    ground = rows - 2
    alive  = []
    for f in frags:
        f[3] += GRAVITY * 1.30
        f[0] += f[2];  f[1] += f[3];  f[4] -= 1
        r, c = int(f[1]), int(f[0])
        if f[4] <= 0 or r >= ground or c < 0 or c >= cols:
            continue
        hit = normals.get((r, c))
        if hit and f[2] * hit[0] + f[3] * hit[1] < 0:
            f[2], f[3] = _reflect_damp(f[2], f[3], hit[0], hit[1])
            f[0] += hit[0] * 0.6;  f[1] += hit[1] * 0.6
        alive.append(f)
    if len(alive) > MAX_FRAGS:
        alive = alive[-MAX_FRAGS:]
    return alive


def _render_frags(frags):
    parts = []
    for f in frags:
        r, c = int(f[1]), int(f[0])
        b = f[4] / FRAG_LIFE
        if   b > 0.65: ch = '\033[1m*\033[0m'
        elif b > 0.35: ch = '\033[1m.\033[0m'
        elif b > 0.14: ch = '\xb7'
        else:          continue
        parts.append(f'\033[{r+1};{c+1}H{ch}')
    return ''.join(parts)


def _new_splash(x):
    return [[float(x), random.choice([-1, 1]) * random.uniform(0.4, 2.0),
             random.randint(3, 8)]
            for _ in range(random.randint(3, 5))]


def _update_splash(sp):
    alive = []
    for s in sp:
        s[0] += s[1];  s[2] -= 1
        if s[2] > 0:
            alive.append(s)
    return alive


# ── Lightning (identical to anim_J_bounce.py) ────────────────────────────────

def _new_bolt(rows, cols, normals, now):
    ground   = rows - 2
    segs     = []
    fork_pts = []
    r, c     = 0, random.randint(cols // 8, cols * 7 // 8)
    while r < ground:
        if (r, c) in normals:
            break
        segs.append((r, c, now))
        if r > rows // 5 and random.random() < 0.14:
            fork_pts.append((r, c))
        r += 1
        c += random.choice([-2, -1, -1, -1, 0, 0, 0, 0, 1, 1, 1, 2])
        c  = max(1, min(cols - 2, c))
    for fr, fc in fork_pts[:2]:
        r = fr + 1
        c = fc + random.choice([-4, -3, 3, 4])
        for _ in range(random.randint(3, max(4, (ground - fr) // 2))):
            if r >= ground or c < 0 or c >= cols or (r, c) in normals:
                break
            segs.append((r, c, now))
            r += 1;  c += random.choice([-1, -1, 0, 0, 0, 1, 1])
            c = max(1, min(cols - 2, c))
    return {'segs': segs,
            'thunder_t':    now + 0.35 + len(segs) * 0.012,
            'thunder_done': False}


def _render_bolts(bolts, now):
    parts = []
    for bolt in bolts:
        for r, c, t in bolt['segs']:
            age = now - t
            if age >= BOLT_LIFE:
                continue
            frac = age / BOLT_LIFE
            ch = '\033[1m#\033[0m' if frac < 0.25 else \
                 '\033[1m+\033[0m' if frac < 0.55 else '*'
            parts.append(f'\033[{r+1};{c+1}H{ch}')
    return ''.join(parts)


# ── Renderer ──────────────────────────────────────────────────────────────────

def _render(rows, cols, chars, drops, gsplashes, frags, bolts, now):
    ground = rows - 2
    buf = [[' '] * cols for _ in range(rows)]

    # Letter outlines
    for (r, c), ch in chars.items():
        if 0 <= r < rows and 0 <= c < cols:
            buf[r][c] = ch

    # Ground floor
    if 0 <= ground < rows:
        for c in range(cols):
            if buf[ground][c] == ' ':
                buf[ground][c] = '_'

    # Drops — char from vx (constant), consistent throughout fall
    for d in drops:
        r, c = int(d[1]), int(d[0])
        if 0 <= r < rows and 0 <= c < cols and buf[r][c] == ' ':
            buf[r][c] = '\\' if d[2] > 0.18 else '|'

    # Ground splashes
    if 0 <= ground < rows:
        for s in gsplashes:
            c = int(s[0])
            if 0 <= c < cols:
                buf[ground][c] = "'" if s[2] > 4 else '.'

    lines = [''.join(row) for row in buf]
    return '\033[H' + '\r\n'.join(lines) + _render_frags(frags) + _render_bolts(bolts, now)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not is_interactive():
        return

    with AltScreen():
        fd, old_tty = tty_enter_raw()
        resize, old_sig = sigwinch_flag()
        try:
            while True:
                cols, rows = shutil.get_terminal_size(fallback=(80, 24))
                rows = max(12, rows - 1)

                normals, chars = _build_letters(rows, cols)
                drops     = [_new_drop(cols) for _ in range(MAX_DROPS // 3)]
                frags:  list = []
                gsplashes: list = []
                first     = True

                next_bolt_t  = time.time() + random.uniform(2.0, 5.0)
                bolts:  list = []
                flash_frames = 0

                while True:
                    if resize[0]:
                        resize[0] = False
                        sys.stdout.write('\033[?5l');  break
                    if key_ready(fd):
                        if os.read(fd, 1) in (b'q', b'Q', b'\x03'):
                            return

                    now = time.time()

                    if now >= next_bolt_t:
                        bolts.append(_new_bolt(rows, cols, normals, now))
                        next_bolt_t  = now + random.uniform(*BOLT_INTERVAL)
                        flash_frames = 2

                    alive_bolts = []
                    for b in bolts:
                        if any(now - t < BOLT_LIFE for _, _, t in b['segs']):
                            alive_bolts.append(b)
                        if not b['thunder_done'] and now >= b['thunder_t']:
                            sys.stdout.write('\a');  b['thunder_done'] = True
                    bolts = alive_bolts

                    if flash_frames > 0:
                        sys.stdout.write('\033[?5h');  flash_frames -= 1
                    else:
                        sys.stdout.write('\033[?5l')

                    drops, new_frags, gsplas = _update_drops(drops, rows, cols, normals)
                    frags = _update_frags(frags + new_frags, rows, cols, normals)
                    for gx in gsplas:
                        gsplashes.extend(_new_splash(gx))
                    gsplashes = _update_splash(gsplashes)

                    out = _render(rows, cols, chars, drops, gsplashes, frags, bolts, now)
                    if first:
                        sys.stdout.write('\033[H\033[2J');  first = False
                    sys.stdout.write(out)
                    sys.stdout.flush()
                    time.sleep(FRAME_S)

        finally:
            sys.stdout.write('\033[?5l')
            tty_restore(fd, old_tty)
            sigwinch_restore(old_sig)


if __name__ == '__main__':
    main()
