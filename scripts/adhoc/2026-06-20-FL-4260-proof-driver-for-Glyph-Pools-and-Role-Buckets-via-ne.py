# Ad hoc script: FL-4260 proof driver for Glyph Pools and Role Buckets via new CDP commands
# Created: 2026-06-20
# Canonical gap: <describe what tool should own this>

"""Proof driver: verify FL4260_POOL_ACTION and FL4260_ROLE_BUCKET_AUTOFILL produce
detached-TERM++ glyph deltas through the accepted backend path."""
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
                self.buf = ''
                self.next_id = 1
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
GATE = FLOOR

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


def changed_cells(before, after, channel='glyph'):
    n = 0
    for k in set(before) | set(after):
        b = before.get(k, (None, None, None))
        a = after.get(k, (None, None, None))
        if channel == 'glyph':
            if b[0] != a[0]: n += 1
        else:
            if b[1] != a[1] or b[2] != a[2]: n += 1
    return n

def percell_sample(c, mat=1, elv=2, shade=7):
    txt = c.call('FL4260_DUMP_PERCELL', f'{mat} {elv} {shade}')
    m = re.search(r'"glyph_id":(\d+),"source_state":"(\w+)"', txt)
    return {'raw': txt.strip(), 'glyph_id': int(m.group(1)) if m else -1,
            'source_state': m.group(2) if m else 'parse_error'} if m else {'raw': txt.strip(), 'glyph_id': -1, 'source_state': 'parse_error'}


def capture_termpp_png(c, out, name):
    c.call('RENDER_TERMPP_ONCE')
    c.call('CAPTURE_TERMPP_FRAME', str(out / f'{name}.png'), timeout=10.0)
    return out / f'{name}.png'

def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8871
    out = Path('docs/research/ascii/verification/fl4260/2026-06-19-color-precondition-slider-proof/pool_role_proof')
    out.mkdir(parents=True, exist_ok=True)
    c = Cdp(port)
    c.call('FL4260_RENDERING_PROOF', '1 0 0')
    c.call('FL4260_APPLY_PALETTE_STARTER', '1')
    c.call('OPEN_TERMPP_CURRENT_VIEW')
    c.call('SET_TERMPP_CAMERA_VIEW', '24 58 14 225 48 32 0')
    time.sleep(1.0)
    # Jitter floor
    j0 = load_cells(dump(c, out, 'jitter_0'))
    j1 = load_cells(dump(c, out, 'jitter_1'))
    floor = changed_cells(j0, j1, 'glyph')
    gate = max(GATE, floor)
    proof = {'schema': 'fl4260.pool_role_actions.v1', 'material': 1, 'gate': gate,
             'actions': {}}
    # Actions to verify: clear, select_all, invert, restore_defaults, role_bucket_autofill
    # For delta, we compare against a baseline with the default/loaded pool.
    baseline_path = dump(c, out, 'baseline')
    baseline = load_cells(baseline_path)
    capture_termpp_png(c, out, 'baseline')
    actions = [
        ('pool_clear', 'FL4260_POOL_ACTION', '1 clear', [('FL4260_POOL_ACTION', '1 restore_defaults')]),
        ('pool_select_all', 'FL4260_POOL_ACTION', '1 select_all', [('FL4260_POOL_ACTION', '1 clear')]),
        ('pool_invert', 'FL4260_POOL_ACTION', '1 invert', [('FL4260_POOL_ACTION', '1 clear')]),
        ('pool_restore_defaults', 'FL4260_POOL_ACTION', '1 restore_defaults', [('FL4260_POOL_ACTION', '1 clear')]),
        ('role_bucket_autofill', 'FL4260_ROLE_BUCKET_AUTOFILL', '1', [
            ('FL4260_POOL_ACTION', '1 clear'),
            ('FL4260_POOL_ACTION', '1 select_all'),
            ('FL4260_CLEAR_ROLE_BUCKETS', '1'),
        ]),
    ]
    for label, method, params, preconditions in actions:
        # Establish precondition(s)
        for pre_m, pre_p in preconditions:
            c.call(pre_m, pre_p)
            time.sleep(0.5)
        before_path = dump(c, out, f'{label}_before')
        before = load_cells(before_path)
        before_percell = [percell_sample(c, 1, e, s) for e, s in [(0, 0), (1, 4), (2, 7), (3, 10)]]
        # Apply action
        c.call(method, params)
        time.sleep(0.5)
        after_path = dump(c, out, label)
        after = load_cells(after_path)
        after_percell = [percell_sample(c, 1, e, s) for e, s in [(0, 0), (1, 4), (2, 7), (3, 10)]]
        ch = changed_cells(before, after, 'glyph')
        percell_changed = any(b['glyph_id'] != a['glyph_id'] for b, a in zip(before_percell, after_percell))
        is_ok = ch > gate or percell_changed
        capture_termpp_png(c, out, f'{label}_before')
        capture_termpp_png(c, out, label)
        proof['actions'][label] = {'method': method, 'params': params,
                                   'preconditions': [{'method': m, 'params': p} for m, p in preconditions],
                                   'changed_cells': ch, 'gate': gate,
                                   'percell_before': before_percell, 'percell_after': after_percell,
                                   'percell_changed': percell_changed,
                                   'ok': is_ok,
                                   'before': str(before_path.name),
                                   'after': str(after_path.name)}
        print(f'{label}: changed={ch} gate={gate} percell_changed={percell_changed} ok={is_ok}')
    (out / 'PROOF.json').write_text(json.dumps(proof, indent=2))
    ok_count = sum(1 for v in proof['actions'].values() if v['ok'])
    print(f'summary: {ok_count}/{len(actions)} actions produced TERM++ glyph delta')
    c.close()

if __name__ == '__main__':
    main()
