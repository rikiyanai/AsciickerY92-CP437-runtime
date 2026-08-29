"""J: Angled ASCII rain — triangle + semicircle bounce, fragmenting droplets, lightning.

No banner required.  Pure ASCII physics simulation.
Rain falls diagonally with varying speed and lean — slow drizzle mixes with fast streaks.

Shapes:
  Triangle (left):    wedge; drops bounce off sloped edges using surface normals.
  Semicircle (right): dome up; drops bounce off the curved arc via radial normals.

On wall impact a drop fragments into FRAG_COUNT bright droplets.  Fragments use
full gravity (not reduced) so they plummet quickly rather than floating upward.

Lightning strikes every BOLT_INTERVAL seconds:
  - Jagged bolt from sky to ground/shape, with random forks.
  - Segment chars age:  bold '#' → bold '+' → '*'  over BOLT_LIFE seconds.
  - Screen inverts for 2 frames on strike (terminal flash — no colour needed).
  - Thunder bell (\a) arrives after a distance-proportional delay.

'q' / Ctrl-C to quit.  Resize restarts.
Run: python3 testing/anim_J_bounce.py
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
MAX_DROPS     = 55
MAX_FRAGS     = 80
GRAVITY       = 0.08
RAIN_VX       = 0.22           # mean diagonal lean — steeper/more vertical
RAIN_VY       = (0.35, 1.80)   # wide range: drizzle → driving rain
BOUNCE_DAMP   = 0.32           # low: fragments spatter close, don't soar
FRAG_COUNT    = 3
FRAG_LIFE     = 14
BOLT_LIFE     = 0.75           # seconds each bolt segment stays visible
BOLT_INTERVAL = (5.0, 13.0)    # seconds between lightning strikes


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _bresenham(r0, c0, r1, c1):
    cells = []
    dr, dc = abs(r1 - r0), abs(c1 - c0)
    sr = 1 if r1 > r0 else -1
    sc = 1 if c1 > c0 else -1
    err = dr - dc
    r, c = r0, c0
    while True:
        cells.append((r, c))
        if r == r1 and c == c1:
            break
        e2 = 2 * err
        if e2 > -dc:
            err -= dc;  r += sr
        if e2 < dr:
            err += dr;  c += sc
    return cells


def _outward_nc_nr(dr, dc, cent_r, cent_c, mid_r, mid_c):
    """Pick the perpendicular to (dr,dc) that points away from (cent_r, cent_c)."""
    mag = math.sqrt(dr * dr + dc * dc)
    for nc, nr in [(-dc / mag, dr / mag), (dc / mag, -dr / mag)]:
        tr, tc = mid_r + nr, mid_c + nc
        if (tr - cent_r) ** 2 + (tc - cent_c) ** 2 > \
           (mid_r - cent_r) ** 2 + (mid_c - cent_c) ** 2:
            return nc, nr
    return dc / mag, -dr / mag


def _reflect_damp(vx, vy, nc, nr):
    """Reflect (vx, vy) off surface normal (nc, nr) and apply BOUNCE_DAMP."""
    dot = vx * nc + vy * nr
    return (vx - 2 * dot * nc) * BOUNCE_DAMP, (vy - 2 * dot * nr) * BOUNCE_DAMP


# ── Build shapes ──────────────────────────────────────────────────────────────

def _build_shapes(rows, cols):
    """Return normals {(r,c):(nc,nr)} and chars {(r,c):char} for all wall cells."""
    normals: dict = {}
    chars:   dict = {}
    ground = rows - 2

    # ── Triangle (left quarter) ───────────────────────────────────────────────
    tri_h    = max(6, min(rows * 5 // 12, 13))
    tri_w    = max(8, int(tri_h * 1.45))
    ap_r     = ground - tri_h
    ap_c     = cols // 4
    bl       = (ground, max(0, ap_c - tri_w // 2))
    br       = (ground, min(cols - 1, ap_c + tri_w // 2))
    apex     = (ap_r, ap_c)
    cent_r   = (ap_r + ground + ground) / 3
    cent_c   = float(ap_c)

    def _add_edge(p0, p1, ch):
        r0, c0 = p0;  r1, c1 = p1
        dr, dc = r1 - r0, c1 - c0
        mag = math.sqrt(dr * dr + dc * dc)
        if mag < 0.5:
            return
        nc, nr = _outward_nc_nr(dr, dc, cent_r, cent_c,
                                (r0 + r1) / 2.0, (c0 + c1) / 2.0)
        for r, c in _bresenham(r0, c0, r1, c1):
            normals[(r, c)] = (nc, nr)
            chars[(r, c)]   = ch

    _add_edge(apex, bl, '/')
    _add_edge(apex, br, '\\')
    _add_edge(bl,   br, '_')
    chars[apex] = '^'

    # ── Semicircle dome (right three-quarters) ────────────────────────────────
    r_row  = max(5, min(rows // 4, 10))
    r_col  = round(r_row * 1.90)
    sc_r   = ground
    sc_c   = (cols * 3) // 4

    seen: set = set()
    steps = max(200, r_row * 14)
    for i in range(steps + 1):
        theta = math.pi * i / steps
        row   = sc_r - round(r_row * math.sin(theta))
        col   = sc_c + round(r_col  * math.cos(theta))
        if (row, col) in seen or not (0 <= col < cols) or row >= sc_r:
            continue
        seen.add((row, col))
        dr_n = (row - sc_r) / r_row
        dc_n = (col - sc_c) / r_col
        mag  = math.sqrt(dr_n * dr_n + dc_n * dc_n)
        normals[(row, col)] = (dc_n / mag if mag > 0.001 else 0.0,
                               dr_n / mag if mag > 0.001 else -1.0)
        t  = theta / math.pi
        ch = '|' if t < 0.10 or t > 0.90 else '\\' if t < 0.30 else '/' if t > 0.70 else '_'
        chars[(row, col)] = ch

    return normals, chars


# ── Drop particles ─────────────────────────────────────────────────────────────

def _new_drop(cols):
    """[x, y, vx, vy] — lean sampled from a Gaussian so rain has organic variety."""
    return [
        float(random.randint(0, max(0, cols - 1))),
        float(random.randint(-14, -1)),
        max(0.0, RAIN_VX + random.gauss(0, 0.12)),  # clamp: no leftward drops
        random.uniform(*RAIN_VY),
    ]


def _update_drops(drops, rows, cols, normals):
    """Returns (alive, new_frags, ground_impact_cols)."""
    ground  = rows - 2
    alive   = []
    frags   = []
    gsplas  = []

    for d in drops:
        d[3] += GRAVITY
        nx, ny = d[0] + d[2], d[1] + d[3]
        if nx < 0 or nx >= cols:
            continue
        r, c = int(ny), int(nx)
        if r >= ground:
            gsplas.append(int(d[0]))
            continue
        hit = normals.get((r, c))
        if hit:
            frags.extend(_spawn_frags(d[0], d[1], d[2], d[3], hit[0], hit[1]))
            continue
        d[0] = nx;  d[1] = ny
        alive.append(d)

    while len(alive) < MAX_DROPS:
        alive.append(_new_drop(cols))
    return alive, frags, gsplas


# ── Fragment particles ─────────────────────────────────────────────────────────

def _spawn_frags(x, y, vx, vy, nc, nr):
    rvx, rvy = _reflect_damp(vx, vy, nc, nr)
    out = []
    for _ in range(FRAG_COUNT):
        out.append([x, y, rvx + random.gauss(0, 0.22),
                          rvy + random.gauss(0, 0.18), FRAG_LIFE])
    return out


def _update_frags(frags, rows, cols, normals):
    ground = rows - 2
    alive  = []
    for f in frags:
        f[3] += GRAVITY * 1.30   # full gravity — fragments plummet, not float
        f[0] += f[2];  f[1] += f[3]
        f[4] -= 1
        r, c = int(f[1]), int(f[0])
        if f[4] <= 0 or r >= ground or c < 0 or c >= cols:
            continue
        hit = normals.get((r, c))
        if hit:
            nc, nr = hit
            if f[2] * nc + f[3] * nr < 0:
                f[2], f[3] = _reflect_damp(f[2], f[3], nc, nr)
                f[0] += nc * 0.6;  f[1] += nr * 0.6
        alive.append(f)
    if len(alive) > MAX_FRAGS:
        alive = alive[-MAX_FRAGS:]
    return alive


def _render_frags(frags):
    parts = []
    for f in frags:
        r, c = int(f[1]), int(f[0])
        b = f[4] / FRAG_LIFE
        if b > 0.65:
            ch = '\033[1m*\033[0m'
        elif b > 0.35:
            ch = '\033[1m.\033[0m'
        elif b > 0.14:
            ch = '\xb7'
        else:
            continue
        parts.append(f'\033[{r+1};{c+1}H{ch}')
    return ''.join(parts)


# ── Ground splashes ────────────────────────────────────────────────────────────

def _new_splash(x):
    out = []
    for _ in range(random.randint(3, 5)):
        out.append([float(x), random.choice([-1, 1]) * random.uniform(0.4, 2.0),
                    random.randint(3, 8)])
    return out


def _update_splash(sp):
    alive = []
    for s in sp:
        s[0] += s[1];  s[2] -= 1
        if s[2] > 0:
            alive.append(s)
    return alive


# ── Lightning ─────────────────────────────────────────────────────────────────

def _new_bolt(rows, cols, normals, now):
    """Generate a jagged bolt from the sky.  Returns bolt dict."""
    ground = rows - 2
    segs   = []   # (row, col, t_born)
    col    = random.randint(cols // 8, cols * 7 // 8)
    fork_pts = []

    r, c = 0, col
    while r < ground:
        if (r, c) in normals:
            break
        segs.append((r, c, now))
        if r > rows // 5 and random.random() < 0.14:
            fork_pts.append((r, c))
        r += 1
        c += random.choice([-2, -1, -1, -1, 0, 0, 0, 0, 1, 1, 1, 2])
        c  = max(1, min(cols - 2, c))

    # Up to 2 forks — shorter, random direction
    for fr, fc in fork_pts[:2]:
        r = fr + 1
        c = fc + random.choice([-4, -3, 3, 4])
        for _ in range(random.randint(3, max(4, (ground - fr) // 2))):
            if r >= ground or c < 0 or c >= cols or (r, c) in normals:
                break
            segs.append((r, c, now))
            r += 1
            c += random.choice([-1, -1, 0, 0, 0, 1, 1])
            c  = max(1, min(cols - 2, c))

    # Thunder delay proportional to bolt height (longer = more distant storm)
    thunder_t = now + 0.35 + len(segs) * 0.012

    return {'segs': segs, 'thunder_t': thunder_t, 'thunder_done': False}


def _render_bolts(bolts, now):
    parts = []
    for bolt in bolts:
        for r, c, t in bolt['segs']:
            age  = now - t
            if age >= BOLT_LIFE:
                continue
            frac = age / BOLT_LIFE
            if frac < 0.25:
                ch = '\033[1m#\033[0m'
            elif frac < 0.55:
                ch = '\033[1m+\033[0m'
            else:
                ch = '*'
            parts.append(f'\033[{r+1};{c+1}H{ch}')
    return ''.join(parts)


# ── Frame renderer ─────────────────────────────────────────────────────────────

def _render(rows, cols, chars, drops, gsplashes, frags, bolts, now):
    ground = rows - 2
    buf = [[' '] * cols for _ in range(rows)]

    # Shapes
    for (r, c), ch in chars.items():
        if 0 <= r < rows and 0 <= c < cols:
            buf[r][c] = ch

    # Ground floor
    if 0 <= ground < rows:
        for c in range(cols):
            if buf[ground][c] == ' ':
                buf[ground][c] = '_'

    # Drops — char from vx (set at spawn, never changes) so each drop looks
    # consistent throughout its fall.  '\\' for leaning, '|' for near-vertical.
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
    base  = '\033[H' + '\r\n'.join(lines)
    return base + _render_frags(frags) + _render_bolts(bolts, now)


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

                normals, chars = _build_shapes(rows, cols)
                drops     = [_new_drop(cols) for _ in range(MAX_DROPS // 3)]
                frags     = []
                gsplashes = []
                first     = True

                # Lightning state
                next_bolt_t  = time.time() + random.uniform(2.0, 5.0)
                bolts: list  = []
                flash_frames = 0

                while True:
                    if resize[0]:
                        resize[0] = False
                        sys.stdout.write('\033[?5l')   # ensure normal video on resize
                        break
                    if key_ready(fd):
                        if os.read(fd, 1) in (b'q', b'Q', b'\x03'):
                            return

                    now = time.time()

                    # ── Lightning trigger ─────────────────────────────────────
                    if now >= next_bolt_t:
                        bolts.append(_new_bolt(rows, cols, normals, now))
                        next_bolt_t  = now + random.uniform(*BOLT_INTERVAL)
                        flash_frames = 2

                    # Expire dead bolts and fire thunder
                    alive_bolts = []
                    for b in bolts:
                        if any(now - t < BOLT_LIFE for _, _, t in b['segs']):
                            alive_bolts.append(b)
                        if not b['thunder_done'] and now >= b['thunder_t']:
                            sys.stdout.write('\a')
                            b['thunder_done'] = True
                    bolts = alive_bolts

                    # ── Screen flash (DECSCNM reverse video) ──────────────────
                    if flash_frames > 0:
                        sys.stdout.write('\033[?5h')
                        flash_frames -= 1
                    else:
                        sys.stdout.write('\033[?5l')   # idempotent — safe every frame

                    # ── Physics ───────────────────────────────────────────────
                    drops, new_frags, gsplas = _update_drops(drops, rows, cols, normals)
                    frags = _update_frags(frags + new_frags, rows, cols, normals)
                    for gx in gsplas:
                        gsplashes.extend(_new_splash(gx))
                    gsplashes = _update_splash(gsplashes)

                    out = _render(rows, cols, chars, drops, gsplashes, frags, bolts, now)
                    if first:
                        sys.stdout.write('\033[H\033[2J')
                        first = False
                    sys.stdout.write(out)
                    sys.stdout.flush()
                    time.sleep(FRAME_S)

        finally:
            sys.stdout.write('\033[?5l')   # guarantee normal video on exit
            tty_restore(fd, old_tty)
            sigwinch_restore(old_sig)


if __name__ == '__main__':
    main()
