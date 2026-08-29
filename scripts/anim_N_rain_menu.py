"""N: rain + scanline intro + live main-menu / submenu navigation.

Built on anim_M (J-physics on the real banner asset) with three additions:

  1. Scanline intro  — banner revealed row-by-row while rain already falls.
  2. Main menu       — items rendered just below the banner with tight margins;
                       each row is erased (\\033[2K) before rewrite so text
                       floats cleanly over the rain background.
  3. Submenu         — pressing 1/2/4/5 replaces the menu lines with a
                           ──── ⚠ Title ────
                       divider + submenu items directly beneath the banner.
                       q returns to the main menu.

Rain (drops, fragments, lightning) never pauses — runs throughout all states.

Keys
  Main menu : 1 / 2 / 4 / 5  → open submenu     q / Q → quit
  Submenu   : q / Q           → back to main menu
  Any state : Ctrl-C          → quit

Resize restarts the whole sequence from the scanline.
Run: python3 testing/anim_N_rain_menu.py
"""
from __future__ import annotations
import math, os, random, shutil, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent))

from _common import (
    AltScreen, is_interactive, key_ready,
    load_banner_pixels, render_banner,
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


def _banner_cols(term_cols: int) -> int:
    """Target banner width: ~55 % of terminal, capped at 60."""
    return max(30, min(term_cols * 55 // 100, 60))


# ── Menu data ─────────────────────────────────────────────────────────────────

MENU_ITEMS = [
    ("1", "GAME",             "single player · multiplayer"),
    ("2", "MAP & EDITOR",     "maps · editor · blender / osm"),
    ("4", "CONFIG & STATUS",  "settings · health probes"),
    ("5", "ASSET PIPELINE",   "sprites · meshes · wizard"),
    ("q", "QUIT",             ""),
]

SUBMENUS: dict[str, tuple[str, list]] = {
    "1": ("Game", [
        ("1", "Single Player", ""),
        ("2", "Multiplayer",   ""),
        ("q", "Back",          ""),
    ]),
    "2": ("Map & Editor", [
        ("1", "ASCIIID EDITOR",  ""),
        ("2", "MESHES",          ""),
        ("3", "MAP DIAGNOSTICS", ""),
        ("4", "BLENDER & OSM",   ""),
        ("q", "Back",            ""),
    ]),
    "4": ("Config & Status", [
        ("1", "Multiplayer",    ""),
        ("2", "Blender Config", ""),
        ("3", "Available MCPs", ""),
        ("q", "Back",           ""),
    ]),
    "5": ("Asset Pipeline", [
        ("1", "Wizard",           ""),
        ("2", "Browse XP Assets", ""),
        ("3", "Workbench",        ""),
        ("q", "Back",             ""),
    ]),
}

_SUBMENU_KEYS = {k.encode() for k in SUBMENUS}


# ── Collision walls from banner pixel alpha (identical to anim_M) ─────────────

def _build_banner_walls(pixels, src_w, src_h, b_cols, row_off, col_off):
    """Return (normals, b_rows).  normals keys are screen-space (row, col)."""
    tgt_w = b_cols
    tgt_h = max(2, int(src_h * tgt_w / src_w))
    if tgt_h % 2:
        tgt_h += 1
    b_rows = tgt_h // 2

    opaque: set = set()
    for gr in range(b_rows):
        for gx in range(tgt_w):
            sx = int(gx * src_w / tgt_w)
            for py in range(2):
                sy = int((gr * 2 + py) * src_h / tgt_h)
                if sy < src_h and pixels[sy * src_w + sx][3] >= 16:
                    opaque.add((gr, gx))
                    break

    normals: dict = {}
    for (r, c) in opaque:
        up    = (r - 1, c) not in opaque
        down  = (r + 1, c) not in opaque
        left  = (r, c - 1) not in opaque
        right = (r, c + 1) not in opaque
        is_boundary = up or down or left or right
        if not is_boundary:
            normals[(r + row_off, c + col_off)] = (0.0, -1.0)
            continue
        nr_s = -1 if up else (1 if down else 0)
        nc_s = (-1 if left else 0) + (1 if right else 0)
        if nr_s == 0 and nc_s == 0:
            nr_s = -1
        mag = math.sqrt(nr_s ** 2 + nc_s ** 2)
        normals[(r + row_off, c + col_off)] = (nc_s / mag, nr_s / mag)

    return normals, b_rows


# ── Physics (identical to anim_M) ─────────────────────────────────────────────

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


# ── Lightning (identical to anim_M) ──────────────────────────────────────────

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


# ── Menu / submenu rendering ──────────────────────────────────────────────────

def _render_main_menu(start_row: int, cols: int) -> str:
    """\033[2K clears each row before writing so rain chars don't bleed through.
    Items are block-centred: all start at the column derived from the widest
    visible line so keys stay aligned while the block sits under the banner."""
    # Compute visible widths (no ANSI codes) to find block width
    visible = []
    for key, label, desc in MENU_ITEMS:
        t = f"[{key}] {label:<16}"
        if desc:
            t += f"  {desc}"
        visible.append(t)

    block_w   = max(len(t) for t in visible)
    col_start = max(1, (cols - block_w) // 2 + 1)   # 1-indexed terminal col

    parts = []
    for i, (key, label, desc) in enumerate(MENU_ITEMS):
        row  = start_row + i
        text = f"[{key}] {label:<16}"
        if desc:
            text += f"  \033[2m{desc}\033[0m"
        # Position at col_start, erase whole line, write text (cursor stays at col_start)
        parts.append(f'\033[{row + 1};{col_start}H\033[2K{text}')
    return ''.join(parts)


def _render_submenu_panel(start_row: int, cols: int,
                          title: str, items: list) -> str:
    """Divider line + items, one row below banner, same \033[2K technique."""
    parts = []

    # ──── ⚠ Title ────  (centered)
    inner   = f" \u26a0 {title} "
    side    = max(4, (cols - len(inner)) // 2)
    divider = "\u2500" * side + inner + "\u2500" * max(0, cols - side - len(inner))
    parts.append(f'\033[{start_row + 1};1H\033[2K{divider}')

    # Block-centre items under the divider
    item_vis  = [f"[{key}] {label}" for key, label, _ in items]
    block_w   = max(len(t) for t in item_vis) if item_vis else 10
    col_start = max(1, (cols - block_w) // 2 + 1)

    for i, text in enumerate(item_vis):
        row = start_row + 1 + i
        parts.append(f'\033[{row + 1};{col_start}H\033[2K{text}')

    return ''.join(parts)


# ── Frame renderer ────────────────────────────────────────────────────────────

def _render(state, scanline_cur,
            rows, cols, banner_lines, b_rows, row_off, col_off, b_cols,
            drops, gsplashes, frags, bolts, now,
            active_submenu, menu_start_row):

    ground = rows - 2
    buf    = [[' '] * cols for _ in range(rows)]

    if 0 <= ground < rows:
        for c in range(cols):
            buf[ground][c] = '_'

    # Drops — skip banner bounding box (banner overlay covers it)
    b_end = row_off + b_rows
    for d in drops:
        r, c = int(d[1]), int(d[0])
        if row_off <= r < b_end and col_off <= c < col_off + b_cols:
            continue
        if 0 <= r < rows and 0 <= c < cols and buf[r][c] == ' ':
            buf[r][c] = '\\' if d[2] > 0.18 else '|'

    if 0 <= ground < rows:
        for s in gsplashes:
            c = int(s[0])
            if 0 <= c < cols:
                buf[ground][c] = "'" if s[2] > 4 else '.'

    out = '\033[H' + '\r\n'.join(''.join(row) for row in buf)

    # Banner overlay — progressively revealed during SCANLINE
    reveal = scanline_cur if state == "SCANLINE" else len(banner_lines)
    for i in range(min(reveal, len(banner_lines))):
        out += f'\033[{row_off + i + 1};{col_off + 1}H{banner_lines[i]}'

    # Menu / submenu overlay (written after banner so they sit on top)
    if state == "MAIN_MENU":
        out += _render_main_menu(menu_start_row, cols)
    elif state == "SUBMENU" and active_submenu:
        title, items = SUBMENUS[active_submenu]
        out += _render_submenu_panel(menu_start_row, cols, title, items)

    return out + _render_frags(frags) + _render_bolts(bolts, now)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not is_interactive():
        return

    pixels, src_w, src_h = load_banner_pixels()
    if not pixels:
        sys.stdout.write('No banner asset found (checked BANNER_PNG and BANNER_XP).\n')
        return

    with AltScreen():
        fd, old_tty = tty_enter_raw()
        resize, old_sig = sigwinch_flag()
        try:
            while True:   # outer loop: re-runs on resize
                cols, rows = shutil.get_terminal_size(fallback=(80, 24))
                rows = max(12, rows - 1)

                b_cols  = _banner_cols(cols)
                col_off = max(0, (cols - b_cols) // 2)
                row_off = max(1, rows // 6)

                normals, b_rows = _build_banner_walls(
                    pixels, src_w, src_h, b_cols, row_off, col_off)

                banner_str, _  = render_banner(pixels, src_w, src_h, b_cols)
                banner_lines   = banner_str.split('\r\n')
                menu_start_row = row_off + b_rows + 1   # 1-row gap below banner

                drops      = [_new_drop(cols) for _ in range(MAX_DROPS // 3)]
                frags:     list = []
                gsplashes: list = []
                first      = True

                next_bolt_t  = time.time() + random.uniform(2.0, 5.0)
                bolts:  list = []
                flash_frames = 0

                # ── State machine ──────────────────────────────────────────────
                state          = "SCANLINE"
                scanline_cur   = 0      # how many banner rows have been revealed
                active_submenu = None   # key into SUBMENUS, or None

                while True:
                    if resize[0]:
                        resize[0] = False
                        sys.stdout.write('\033[?5l')
                        break   # restart outer loop → reinitialise for new size

                    # ── Input ──────────────────────────────────────────────────
                    if key_ready(fd):
                        ch = os.read(fd, 1)
                        if ch == b'\x03':                   # Ctrl-C → always quit
                            return
                        elif ch in (b'q', b'Q'):
                            if state == "SUBMENU":
                                state = "MAIN_MENU"
                            else:
                                return
                        elif state == "MAIN_MENU" and ch in _SUBMENU_KEYS:
                            active_submenu = ch.decode()
                            state = "SUBMENU"

                    # ── Scanline advance (one row per frame) ───────────────────
                    if state == "SCANLINE":
                        scanline_cur += 1
                        if scanline_cur >= len(banner_lines):
                            state = "MAIN_MENU"

                    now = time.time()

                    # ── Lightning ──────────────────────────────────────────────
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

                    # ── Physics ────────────────────────────────────────────────
                    drops, new_frags, gsplas = _update_drops(drops, rows, cols, normals)
                    frags = _update_frags(frags + new_frags, rows, cols, normals)
                    for gx in gsplas:
                        gsplashes.extend(_new_splash(gx))
                    gsplashes = _update_splash(gsplashes)

                    # ── Render ─────────────────────────────────────────────────
                    out = _render(state, scanline_cur,
                                  rows, cols, banner_lines, b_rows,
                                  row_off, col_off, b_cols,
                                  drops, gsplashes, frags, bolts, now,
                                  active_submenu, menu_start_row)

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
