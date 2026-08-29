# Ad hoc script: FL-4260 proof driver for ColorEdit3 fg/bg rows and Clear colors via new CDP commands
# Created: 2026-06-20
# Canonical gap: <describe what tool should own this>

"""Proof driver: verify FL4260_SET_ROW_COLOR and Clear colors produce detached-TERM++
color deltas through the accepted backend path."""
import socket, time, json, re, sys
from pathlib import Path

class Cdp:
    def __init__(self, port, timeout=30.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.settimeout(0.5)
                self.sock.connect(('127.0.0.1', port))
                self.buf = ''; self.next_id = 1
                return
            except Exception:
                time.sleep(0.3)
        raise RuntimeError('CDP not ready')
    def call(self, method, params='', timeout=10.0):
        i = self.next_id; self.next_id += 1
        self.sock.sendall((json.dumps({'id': i, 'method': method, 'params': params}) + '\n').encode())
        end = time.time() + timeout
        while time.time() < end:
            self.sock.settimeout(max(0.05, end - time.time()))
            try: chunk = self.sock.recv(65536).decode('utf-8', 'replace')
            except socket.timeout: continue
            if not chunk: raise RuntimeError('socket closed')
            self.buf += chunk
            while '\n' in self.buf:
                ln, self.buf = self.buf.split('\n', 1)
                if not ln.strip(): continue
                try: msg = json.loads(ln)
                except Exception: continue
                if msg.get('id') == i: return str(msg.get('result', ''))
        raise TimeoutError(method)
    def close(self):
        try: self.sock.close()
        except Exception: pass

FLOOR = 8

def dump(c, out, name):
    out.mkdir(parents=True, exist_ok=True)
    dst = (out / f"{name}.jsonl").resolve()
    if dst.exists(): dst.unlink()
    c.call("RENDER_TERMPP_ONCE", "", timeout=20.0)
    time.sleep(0.2)
    c.call("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(dst), timeout=20.0)
    end = time.time() + 10
    while time.time() < end and not dst.exists(): time.sleep(0.1)
    return dst

def load_cells(path):
    g = {}
    if not path.exists(): return g
    for ln in Path(path).read_text(errors="replace").splitlines():
        ln = ln.strip()
        if not ln or ln[0] != "{": continue
        try: o = json.loads(ln)
        except Exception: continue
        if o.get("kind") != "cell": continue
        x = o.get("x"); y = o.get("y")
        if x is None or y is None: continue
        g[(x, y)] = (o.get("final_gid"), o.get("fg"), o.get("bk"))
    return g

def changed_cells(before, after):
    n = 0
    for k in set(before) | set(after):
        b = before.get(k, (None, None, None))
        a = after.get(k, (None, None, None))
        if b[1] != a[1] or b[2] != a[2]: n += 1
    return n

def profile_color_status(c, mat, elv, shade):
    txt = c.call('FL4260_PROFILE_COLOR_STATUS', f'{mat} {elv} {shade}')
    m = re.search(r'ok=(\d+) fg=(\d+) bg=(\d+)', txt)
    return {'ok': int(m.group(1)) if m else 0,
            'fg': int(m.group(2)) if m else -1,
            'bg': int(m.group(3)) if m else -1,
            'raw': txt.strip()}

def capture_termpp_png(c, out, name):
    c.call('RENDER_TERMPP_ONCE')
    c.call('CAPTURE_TERMPP_FRAME', str(out / f'{name}.png'), timeout=10.0)
    return out / f'{name}.png'

def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8881
    out = Path('docs/research/ascii/verification/fl4260/2026-06-19-color-precondition-slider-proof/coloredit3_proof')
    out.mkdir(parents=True, exist_ok=True)
    c = Cdp(port)
    c.call('FL4260_RENDERING_PROOF', '1 0 0')
    c.call('FL4260_APPLY_PALETTE_STARTER', '1')
    c.call('OPEN_TERMPP_CURRENT_VIEW')
    c.call('SET_TERMPP_CAMERA_VIEW', '24 58 14 225 48 32 0')
    time.sleep(1.0)
    # Jitter
    j0 = load_cells(dump(c, out, 'jitter_0'))
    j1 = load_cells(dump(c, out, 'jitter_1'))
    gate = max(FLOOR, changed_cells(j0, j1))
    proof = {'schema': 'fl4260.coloredit3_and_clear.v1', 'material': 1, 'gate': gate, 'actions': {}}
    # 8 ColorEdit3 rows: fg/bg for r0..r3
    tests = []
    for r in range(4):
        tests.append((f'row{r}_fg', 'fg', r, (255, 0, 0)))
        tests.append((f'row{r}_bg', 'bg', r, (0, 0, 255)))
    for label, channel, row, rgb in tests:
        shade = {0: 0, 1: 4, 2: 7, 3: 10}[row]
        before_color = profile_color_status(c, 1, row, shade)
        before_path = dump(c, out, f'{label}_before')
        before = load_cells(before_path)
        r, g, b = rgb
        c.call('FL4260_SET_ROW_COLOR', f'1 {row} {channel} {r} {g} {b}')
        time.sleep(0.5)
        after_color = profile_color_status(c, 1, row, shade)
        after_path = dump(c, out, label)
        after = load_cells(after_path)
        ch = changed_cells(before, after)
        color_changed = (before_color['fg'] != after_color['fg'] or before_color['bg'] != after_color['bg'])
        is_ok = ch > gate or color_changed
        capture_termpp_png(c, out, f'{label}_before')
        capture_termpp_png(c, out, label)
        proof['actions'][label] = {'channel': channel, 'row': row, 'rgb': rgb,
                                   'color_before': before_color, 'color_after': after_color,
                                   'changed_cells': ch, 'gate': gate, 'ok': is_ok,
                                   'before': str(before_path.name), 'after': str(after_path.name)}
        print(f'{label}: changed={ch} color_changed={color_changed} ok={is_ok}')
    # Clear colors: compare starter-active colors vs all-black cleared colors
    c.call('FL4260_APPLY_PALETTE_STARTER', '1')
    before_path = dump(c, out, 'clear_before')
    before = load_cells(before_path)
    c.call('FL4260_CLEAR_COLORS', '1')
    time.sleep(0.5)
    after_path = dump(c, out, 'clear_after')
    after = load_cells(after_path)
    ch = changed_cells(before, after)
    is_ok = ch > gate
    capture_termpp_png(c, out, 'clear_before')
    capture_termpp_png(c, out, 'clear_after')
    proof['actions']['clear_colors'] = {'method': 'FL4260_CLEAR_COLORS', 'params': '1',
                                         'changed_cells': ch, 'gate': gate, 'ok': is_ok,
                                         'before': str(before_path.name), 'after': str(after_path.name)}
    print(f'clear_colors: changed={ch} gate={gate} ok={is_ok}')
    (out / 'PROOF.json').write_text(json.dumps(proof, indent=2))
    ok_count = sum(1 for v in proof['actions'].values() if v['ok'])
    print(f'summary: {ok_count}/{len(tests) + 1} ColorEdit3 rows + Clear colors verified')
    c.close()

if __name__ == '__main__':
    main()
